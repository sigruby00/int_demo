#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import socket
import signal
import struct
import subprocess
import threading
import socketio

# 사용자 정의 config
import config as cfg
from config import ca_id, to_id, AP_INFO

# 설정 상수
SERVER_URL = "http://10.100.30.241:6789"
USE_INTERFACE_ETH = "eth0"
USE_INTERFACE_WLAN = "wlan0"
CAMERA_DEVICE = "/dev/video0"
CAMERA_WIDTH = 1920
CAMERA_HEIGHT = 1080
CAMERA_FPS = 30
CAMERA_PORT = 5000
UDP_PORT = 5001
UDP_BITRATE_MBPS = 15.0
TARGET_TO_IP = next((item['to_ip'] for item in cfg.TO_IP_LIST if item['to_id'] == to_id), None)

robot_id = ca_id
scan_lock = threading.Lock()
last_handover_time = 0
rssi_history = {}
MOVING_AVG_N = 4

# 전역 객체
camera = None
udpgen = None

# ----------- Network Interface Utils --------------
def get_ip_from_interface(iface):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(
            struct.pack('256s', bytes(iface[:15], 'utf-8'))[20:24]
        )
    except Exception:
        result = subprocess.getoutput(f"ip addr show dev {iface}")
        for line in result.splitlines():
            if "inet " in line:
                return line.strip().split()[1].split("/")[0]
    return "0.0.0.0"

# ----------- Camera Streamer ----------------------
class CameraStreamer:
    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()

    def start(self, bind_ip):
        self.stop()
        cmd = [
            'gst-launch-1.0',
            'v4l2src', f'device={CAMERA_DEVICE}',
            '!', f'video/x-h264,width={CAMERA_WIDTH},height={CAMERA_HEIGHT},framerate={CAMERA_FPS}/1',
            '!', 'rtph264pay', 'config-interval=1', 'pt=96',
            '!', f'udpsink host={TARGET_TO_IP} port={CAMERA_PORT} bind-address={bind_ip} sync=false async=false'
        ]
        print(f"[Camera] launching: {' '.join(cmd)}")
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc = None

# ----------- UDP Generator ------------------------
class UDPGenerator(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True
        self.iface = USE_INTERFACE_ETH
        self.packet_size = 1200
        self.interval = (self.packet_size * 8) / (UDP_BITRATE_MBPS * 1e6)
        self.lock = threading.Lock()

    def update(self, iface):
        with self.lock:
            self.iface = iface

    def run(self):
        while self.running:
            try:
                with self.lock:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.setsockopt(socket.SOL_SOCKET, 25, bytes(f"{self.iface}\0", "utf-8"))
                    sock.bind((get_ip_from_interface(self.iface), 0))
                    dst = (TARGET_TO_IP, UDP_PORT)
                while self.running:
                    sock.sendto(os.urandom(self.packet_size), dst)
                    time.sleep(self.interval)
            except Exception as e:
                print(f"[UDP] Error: {e}")
                time.sleep(1)

    def stop(self):
        self.running = False

# ----------- WiFi Functions -----------------------
def get_current_bssid():
    try:
        output = subprocess.check_output(["sudo", "wpa_cli", "status"], text=True)
        for line in output.splitlines():
            if line.startswith("bssid="):
                return line.split("=", 1)[1].strip().lower()
    except subprocess.CalledProcessError:
        pass
    return None

def get_ap_id_from_bssid(bssid):
    for ap in AP_INFO.values():
        if ap['bssid'].lower() == bssid.lower():
            return ap['ap_id']
    return None

def get_rssi_map_from_scan_results():
    global rssi_history
    try:
        output = subprocess.check_output(["sudo", "wpa_cli", "scan_results"], text=True)
    except subprocess.CalledProcessError:
        return {}

    rssi_map = {}
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            bssid, signal = parts[0], parts[2]
            try:
                rssi_val = float(signal)
                history = rssi_history.setdefault(bssid, [])
                history.append(rssi_val)
                if len(history) > MOVING_AVG_N:
                    history.pop(0)
                avg = sum(history) / len(history)
                rssi_map[bssid.lower()] = avg
            except:
                continue
    return rssi_map

def handover_ap(target_bssid):
    global last_handover_time, camera, udpgen
    try:
        subprocess.run(["sudo", "wpa_cli", "roam", target_bssid], check=True)
        subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bssid", target_bssid], check=True)
        subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bgscan", ""], check=True)
        print(f"Successfully handed over to BSSID: {target_bssid}")
        last_handover_time = time.time()

        # ✅ 인터페이스 전환
        iface = USE_INTERFACE_WLAN
        new_ip = get_ip_from_interface(iface)
        camera.start(bind_ip=new_ip)
        udpgen.update(iface=iface)

    except Exception as e:
        print(f"[HO] Error during handover: {e}")

# ----------- Socket.IO ----------------------------
sio = socketio.Client(reconnection=True, reconnection_attempts=0,
                      reconnection_delay=0.1, reconnection_delay_max=0.5)

@sio.event
def connect():
    print('✅ Connected to server')

@sio.event
def disconnect():
    print('❌ Disconnected from server')

@sio.event
def command(data):
    if data.get('robot_id') == str(robot_id):
        handover = data.get('handover')
        if handover:
            handover_id = int(handover, 0)

            if handover_id == 0:
                print(f"[{robot_id}] Handover ID is 0 → Use eth0, no Wi-Fi handover")
                iface = USE_INTERFACE_ETH
                local_ip = get_ip_from_interface(iface)
                camera.start(bind_ip=local_ip)
                udpgen.update(iface=iface)
                return

            # Otherwise: perform Wi-Fi handover
            target_bssid = AP_INFO[handover_id]['bssid'].lower()
            print(f"[{robot_id}] Received handover request to BSSID: {target_bssid}")
            current_bssid = get_current_bssid()

            if current_bssid == target_bssid:
                print(f"[{robot_id}] Already connected to {current_bssid}. Skip HO.")
                return

            if scan_lock.acquire(timeout=5):
                try:
                    handover_ap(target_bssid)  # 내부에서 wlan0로 전환 수행됨
                finally:
                    scan_lock.release()

def sensing_loop():
    while True:
        try:
            cur_bssid = get_current_bssid()
            cur_ap_id = get_ap_id_from_bssid(cur_bssid)
            rssi_map = get_rssi_map_from_scan_results()

            connections = [
                {
                    "gateway_id": gw_id,
                    "mac_address": AP_INFO[gw_id]['bssid'],
                    "connected": str(gw_id == cur_ap_id).lower(),
                    "rssi": rssi_map.get(AP_INFO[gw_id]['bssid'].lower(), -100),
                }
                for gw_id in AP_INFO.keys()
            ]

            if all(conn["connected"] == "false" for conn in connections):
                time.sleep(1.0)
                continue

            sensing_data = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data": {
                    "robot_id": robot_id,
                    "connections": connections
                }
            }
            sio.emit("robot_ss_data", json.dumps(sensing_data).encode())
            time.sleep(1.0)
        except Exception as e:
            print(f"[Sensing] error: {e}")
            time.sleep(1)

def scan_loop():
    while True:
        if time.time() - last_handover_time < 3:
            time.sleep(1)
            continue
        if scan_lock.acquire(blocking=False):
            try:
                subprocess.run(["sudo", "wpa_cli", "scan"], check=True)
                time.sleep(2.0)
            except:
                pass
            finally:
                scan_lock.release()
        time.sleep(1)

# ----------- MAIN ----------------------------
def main():
    global camera, udpgen
    camera = CameraStreamer()
    udpgen = UDPGenerator()

    default_ip = get_ip_from_interface(USE_INTERFACE_ETH)
    camera.start(bind_ip=default_ip)
    udpgen.start()

    threading.Thread(target=sensing_loop, daemon=True).start()
    threading.Thread(target=scan_loop, daemon=True).start()

    try:
        sio.connect(SERVER_URL, auth={"robot_id": str(robot_id)})
    except Exception as e:
        print(f"[SIO] initial connect failed: {e}")

    while True:
        time.sleep(5)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    main()