#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import socket
import signal
import subprocess
import threading
import socketio
from urllib.parse import urlparse

# 사용자 정의 config
import config as cfg
from config import ca_id, to_id

AP_INFO = {
    1: {'ap_id':1, 'bssid': '20:23:51:55:0f:77'},
    2: {'ap_id':2, 'bssid': 'ec:5a:31:a1:4a:a9'},
    3: {'ap_id':3, 'bssid': '84:e8:cb:37:75:59'},
}

TO_IP_LIST = [
    {"to_id": i, "to_ip": f"10.100.30.{20+i}"} for i in range(2, 11)
]

SERVER_URL = "http://10.100.30.241:6789"
USE_INTERFACE_ETH = "eth0"
USE_INTERFACE_WLAN = "wlan0"
CAMERA_DEVICE = "/dev/video2"
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CAMERA_FPS = 30
CAMERA_PORT = 5000
UDP_PORT = 6001
UDP_BITRATE_MBPS = 85.0
TARGET_TO_IP = next((item['to_ip'] for item in TO_IP_LIST if item['to_id'] == to_id), None)

GW_OVERRIDE = {
    "wlan0": "192.168.101.1",
    "eth0": "192.168.11.1",
}

robot_id = ca_id
scan_lock = threading.Lock()
last_handover_time = 0
rssi_history = {}
camera = None
udpgen = None
MOVING_AVG_N = 4

sio = socketio.Client(reconnection=False)
is_connecting = False

# ----------------- Utilities -----------------
def sh(cmd: list, check=True, capture=False):
    try:
        if capture:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
        subprocess.run(cmd, check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return ""
    except subprocess.CalledProcessError as e:
        print(f"[sh] error: {' '.join(cmd)}\n{e.output if hasattr(e, 'output') else e}")
        if check:
            raise
        return ""

def get_ip_from_interface(iface):
    result = subprocess.getoutput(f"ip addr show dev {iface}")
    for line in result.splitlines():
        if "inet " in line:
            return line.strip().split()[1].split("/")[0]
    return "0.0.0.0"

def host_from_url(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""

def resolve_host_to_ip(host: str) -> str:
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    try:
        return socket.gethostbyname(host)
    except Exception:
        return host

def get_gw_for_iface(iface: str) -> str:
    out = subprocess.getoutput("ip -4 route show default")
    for line in out.splitlines():
        if f" dev {iface} " in f" {line} " or line.strip().endswith(f" dev {iface}"):
            parts = line.split()
            if "via" in parts:
                return parts[parts.index("via")+1]
    return ""

def route_replace_host(dest_ip: str, iface: str):
    gw = GW_OVERRIDE.get(iface, "") or get_gw_for_iface(iface)
    if gw:
        cmd = ["sudo", "ip", "route", "replace", f"{dest_ip}/32", "via", gw, "dev", iface]
    else:
        cmd = ["sudo", "ip", "route", "replace", f"{dest_ip}/32", "dev", iface, "scope", "link"]
    sh(cmd, check=False)
    got = sh(["ip", "route", "get", dest_ip], check=False, capture=True)
    if got:
        print(f"[ROUTE] {dest_ip} -> {got}")

# ----------------- Camera Streamer -----------------
class CameraStreamer:
    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()

    def start(self, iface, bind_ip):
        self.stop()
        route_replace_host(TARGET_TO_IP, iface)
        cmd = [
            "gst-launch-1.0", "v4l2src", f"device={CAMERA_DEVICE}", "!",
            f"video/x-h264,width={CAMERA_WIDTH},height={CAMERA_HEIGHT},framerate={CAMERA_FPS}/1", "!",
            "h264parse", "!", "rtph264pay", "config-interval=1", "pt=96", "!",
            "udpsink", f"host={TARGET_TO_IP}", f"port={CAMERA_PORT}", f"bind-address={bind_ip}", "sync=false"
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

# ----------------- UDP Generator -----------------
class UDPGenerator(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True
        self.iface = USE_INTERFACE_ETH
        self.packet_size = 1200
        self.interval = (self.packet_size * 8) / (UDP_BITRATE_MBPS * 1e6)
        self.lock = threading.Lock()
        self.sock = None

    def update(self, iface):
        with self.lock:
            self.iface = iface
            if self.sock:
                self.sock.close()
                self.sock = None

    def run(self):
        while self.running:
            try:
                with self.lock:
                    if self.sock is None:
                        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        self.sock.setsockopt(socket.SOL_SOCKET, 25, bytes(f"{self.iface}\0", "utf-8"))
                        ip = get_ip_from_interface(self.iface)
                        if ip == "0.0.0.0":
                            print(f"[UDP] No IP for {self.iface}")
                            time.sleep(1)
                            continue
                        self.sock.bind((ip, 0))
                        print(f"[UDP] Bound to {self.iface} ({ip})")
                self.sock.sendto(os.urandom(self.packet_size), (TARGET_TO_IP, UDP_PORT))
                time.sleep(self.interval)
            except Exception as e:
                print(f"[UDP] Error: {e}")
                time.sleep(1)

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
            self.sock = None

# ----------------- Wi-Fi -----------------
def get_current_bssid():
    try:
        out = subprocess.check_output(["sudo", "wpa_cli", "status"], text=True)
        for line in out.splitlines():
            if line.startswith("bssid="):
                return line.split("=", 1)[1].strip().lower()
    except:
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
    except:
        return {}
    rssi_map = {}
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            bssid, signal = parts[0], parts[2]
            try:
                rssi_val = float(signal)
                hist = rssi_history.setdefault(bssid, [])
                hist.append(rssi_val)
                if len(hist) > MOVING_AVG_N:
                    hist.pop(0)
                rssi_map[bssid.lower()] = sum(hist) / len(hist)
            except:
                continue
    return rssi_map

def handover_ap(target_bssid):
    global last_handover_time, camera, udpgen
    try:
        print(f"[HO] Trying roam → {target_bssid}")
        subprocess.run(["sudo", "wpa_cli", "roam", target_bssid], check=True)
        subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bssid", target_bssid], check=True)
        subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bgscan", ""], check=True)
        for _ in range(10):
            if get_current_bssid() == target_bssid.lower():
                break
            time.sleep(1)
        else:
            print(f"❌ Handover to {target_bssid} failed (timeout)")
            return
        last_handover_time = time.time()
        for _ in range(10):
            new_ip = get_ip_from_interface(USE_INTERFACE_WLAN)
            if new_ip != "0.0.0.0":
                break
            time.sleep(1)
        else:
            print(f"⚠️ Got BSSID {target_bssid}, but no IP on wlan0")
            return
        print(f"[HO] Camera bind_ip={new_ip}, UDP iface={USE_INTERFACE_WLAN}")
        route_replace_host(TARGET_TO_IP, USE_INTERFACE_WLAN)
        camera.start(iface=USE_INTERFACE_WLAN, bind_ip=new_ip)
        udpgen.update(iface=USE_INTERFACE_WLAN)
    except Exception as e:
        print(f"[HO] Error: {e}")

# ----------------- Sensing & Scan Loop -----------------
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
            sensing_data = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data": {
                    "robot_id": robot_id,
                    "connections": connections
                }
            }
            if sio.connected:
                sio.emit("robot_ss_data", sensing_data)
            else:
                print("[Sensing] Socket.IO not connected. Skipping emit.")
        except Exception as e:
            print(f"[Sensing] error: {e}")
        time.sleep(10)

def scan_loop():
    while True:
        if time.time() - last_handover_time < 3:
            time.sleep(1)
            continue
        if scan_lock.acquire(blocking=False):
            try:
                subprocess.run(["sudo", "wpa_cli", "scan"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except:
                pass
            finally:
                scan_lock.release()
        time.sleep(10)

# ----------------- Socket.IO Handlers -----------------
@sio.event
def connect():
    print("✅ Socket.IO connected")

@sio.event
def disconnect():
    print("❌ Socket.IO disconnected")

# Optional: handle `reboot`, `command` if needed...

# ----------------- Reconnect Logic -----------------
def reconnect_socket():
    global is_connecting
    if is_connecting or sio.connected:
        return
    is_connecting = True
    try:
        for attempt in range(5):
            try:
                sio.connect(SERVER_URL, auth={"robot_id": str(robot_id)})
                print("✅ Connected to server")
                return
            except Exception as e:
                print(f"[Reconnect attempt {attempt+1}] {e}")
                time.sleep(3)
        print("❌ Could not connect after retries.")
    finally:
        is_connecting = False

def socketio_reconnect_watchdog():
    while True:
        if not sio.connected:
            print("[Watchdog] Disconnected. Trying to reconnect...")
            reconnect_socket()
        time.sleep(5)

# ----------------- MAIN -----------------
def main():
    global camera, udpgen
    camera = CameraStreamer()
    udpgen = UDPGenerator()

    server_host = host_from_url(SERVER_URL)
    server_ip = resolve_host_to_ip(server_host) if server_host else ""
    if server_ip:
        route_replace_host(server_ip, USE_INTERFACE_ETH)

    default_ip = get_ip_from_interface(USE_INTERFACE_ETH)
    route_replace_host(TARGET_TO_IP, USE_INTERFACE_ETH)
    camera.start(iface=USE_INTERFACE_ETH, bind_ip=default_ip)
    udpgen.start()

    threading.Thread(target=socketio_reconnect_watchdog, daemon=True).start()
    threading.Thread(target=sensing_loop, daemon=True).start()
    threading.Thread(target=scan_loop, daemon=True).start()

    reconnect_socket()

    while True:
        time.sleep(10)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    main()