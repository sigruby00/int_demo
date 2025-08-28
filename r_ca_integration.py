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
    # 1: {'ap_id':1, 'bssid': 'ec:5a:31:99:ee:99'},
    # 1: {'ap_id':1, 'bssid': '96:5a:31:5d:62:92'},
    1: {'ap_id':1, 'bssid': '20:23:51:55:0f:77'}, # home IP
    2: {'ap_id':2, 'bssid': 'ec:5a:31:a1:4a:a9'},
    3: {'ap_id':3, 'bssid': '84:e8:cb:37:75:59'},
}

TO_IP_LIST = [
    {"to_id": 2, "to_ip": "10.100.30.21"},
    {"to_id": 3, "to_ip": "10.100.30.22"},
    {"to_id": 4, "to_ip": "10.100.30.23"},
    {"to_id": 5, "to_ip": "10.100.30.24"},
    {"to_id": 6, "to_ip": "10.100.30.25"},
    {"to_id": 7, "to_ip": "10.100.30.26"},
    {"to_id": 8, "to_ip": "10.100.30.27"},
    {"to_id": 9, "to_ip": "10.100.30.28"},
    {"to_id": 10, "to_ip": "10.100.30.21"}
]

# 설정 상수
SERVER_URL = "http://10.100.30.241:6789"  # JGN (NeuroRAT Server)
# SERVER_URL = "https://877fe9913d2c.ngrok.app" # JGN (NeuroRAT Server)
USE_INTERFACE_ETH = "eth0"
USE_INTERFACE_WLAN = "wlan0"
CAMERA_DEVICE = "/dev/video2"
CAMERA_WIDTH = 1920
CAMERA_HEIGHT = 1080
CAMERA_FPS = 30
CAMERA_PORT = 5000
UDP_PORT = 6001
UDP_BITRATE_MBPS = 50
TARGET_TO_IP = next((item['to_ip'] for item in TO_IP_LIST if item['to_id'] == to_id), None)

# 설정 상수 아래쪽 아무 데나 추가
GW_OVERRIDE = {
    "wlan0": "192.168.101.1",   # wlan0은 이 GW로 강제
    "eth0": "192.168.11.1",    # 필요하면 다른 인터페이스도 지정 가능
}

print(TARGET_TO_IP)

robot_id = ca_id
scan_lock = threading.Lock()
last_handover_time = 0
rssi_history = {}
MOVING_AVG_N = 4

# 전역 객체
camera = None
udpgen = None

# ----------- Utils --------------
def sh(cmd: list, check=True, capture=False):
    """작은 헬퍼: 쉘 커맨드 실행"""
    try:
        if capture:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
            return out.strip()
        else:
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
    # host가 이미 IP면 그대로 반환
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    # DNS → IPv4
    try:
        return socket.gethostbyname(host)
    except Exception:
        return host

def get_gw_for_iface(iface: str) -> str:
    """
    해당 인터페이스의 default gateway(IP) 조회.
    """
    out = subprocess.getoutput("ip -4 route show default")
    for line in out.splitlines():
        # 예: "default via 10.100.30.1 dev eth0 proto dhcp ..."
        if f" dev {iface} " in f" {line} " or line.strip().endswith(f" dev {iface}"):
            parts = line.split()
            if "via" in parts:
                return parts[parts.index("via")+1]
    return ""  # 게이트웨이 없으면 빈 문자열


def route_replace_host(dest_ip: str, iface: str):
    """
    목적지 단일 IP를 지정 NIC로 라우트 강제.
    - iface에 대한 GW 오버라이드가 있으면 그걸 via로 사용
    - 없으면 해당 iface의 default gateway를 탐색
    - 둘 다 없으면 on-link 전송(scope link)
    """
    if not dest_ip or not iface:
        return

    # ① 오버라이드 우선
    gw = GW_OVERRIDE.get(iface, "")

    # ② 오버라이드 없으면 시스템 라우팅 테이블에서 GW 추출
    if not gw:
        gw = get_gw_for_iface(iface)

    if gw:
        cmd = ["sudo", "ip", "route", "replace", f"{dest_ip}/32", "via", gw, "dev", iface]
    else:
        cmd = ["sudo", "ip", "route", "replace", f"{dest_ip}/32", "dev", iface, "scope", "link"]

    sh(cmd, check=False)
    got = sh(["ip", "route", "get", dest_ip], check=False, capture=True)
    if got:
        print(f"[ROUTE] {dest_ip} -> {got}")

# ----------- Camera Streamer ----------------------
class CameraStreamer:
    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()

    def start(self, iface, bind_ip):
        self.stop()
        # 스트림 목적지 IP를 지정 인터페이스로 강제 라우팅
        route_replace_host(TARGET_TO_IP, iface)

        cmd = [
            "gst-launch-1.0",
            "v4l2src", f"device={CAMERA_DEVICE}", "!",
            f"video/x-h264,width={CAMERA_WIDTH},height={CAMERA_HEIGHT},framerate={CAMERA_FPS}/1",
            "!",
            "h264parse", "!",
            "rtph264pay", "config-interval=1", "pt=96", "!",
            "udpsink", f"host={TARGET_TO_IP}", f"port={CAMERA_PORT}",
                       f"bind-address={bind_ip}", "sync=false"
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
        self.sock = None  # 소켓 멤버 유지

    def update(self, iface):
        with self.lock:
            self.iface = iface
            if self.sock:
                try:
                    self.sock.close()
                except:
                    pass
                self.sock = None

    def run(self):
        while self.running:
            try:
                with self.lock:
                    if self.sock is None:
                        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        # SO_BINDTODEVICE: 해당 NIC로 강제 송신
                        self.sock.setsockopt(socket.SOL_SOCKET, 25, bytes(f"{self.iface}\0", "utf-8"))
                        self.sock.bind((get_ip_from_interface(self.iface), 0))
                        print(f"[UDP] New socket bound to {self.iface} ({get_ip_from_interface(self.iface)})")

                    dst = (TARGET_TO_IP, UDP_PORT)

                # send loop
                self.sock.sendto(os.urandom(self.packet_size), dst)
                time.sleep(self.interval)
            except Exception as e:
                print(f"[UDP] Error: {e}")
                time.sleep(1)

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
            self.sock = None

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

# def handover_ap(target_bssid):
#     global last_handover_time, camera, udpgen
#     try:
#         subprocess.run(["sudo", "wpa_cli", "roam", target_bssid], check=True)
#         subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bssid", target_bssid], check=True)
#         subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bgscan", ""], check=True)
#         print(f"Successfully handed over to BSSID: {target_bssid}")
#         last_handover_time = time.time()

#         # ✅ 인터페이스 전환: WLAN 사용
#         iface = USE_INTERFACE_WLAN
#         new_ip = get_ip_from_interface(iface)
#         print(f"[HO] Camera bind_ip={new_ip}, UDP iface={iface}")

#         # 스트림 목적지 라우트 wlan0으로 강제
#         route_replace_host(TARGET_TO_IP, iface)

#         # 카메라/UDP 경로 전환
#         camera.start(iface=iface, bind_ip=new_ip)
#         udpgen.update(iface=iface)

#     except Exception as e:
#         print(f"[HO] Error during handover: {e}")

# def handover_ap(target_bssid):
#     global last_handover_time, camera, udpgen
#     try:
#         # roam 시도
#         print(f"[HO] Trying roam → {target_bssid}")
#         res = subprocess.run(["sudo", "wpa_cli", "roam", target_bssid], capture_output=True, text=True)
#         print(f"[HO] roam stdout: {res.stdout}, stderr: {res.stderr}, return={res.returncode}")
#         res.check_returncode()
#         subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bssid", target_bssid], check=True)
#         subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bgscan", ""], check=True)

#         # ✅ 실제로 target_bssid 로 붙을 때까지 확인 (최대 8초 대기)
#         for i in range(8):
#             cur_bssid = get_current_bssid()
#             if cur_bssid and cur_bssid.lower() == target_bssid.lower():
#                 print(f"✅ Connected to {target_bssid}")
#                 break
#             time.sleep(1)
#         else:
#             print(f"❌ Handover to {target_bssid} failed (timeout)")
#             return

#         last_handover_time = time.time()
#         print(f"Successfully handed over to BSSID: {target_bssid}")

#         # ✅ 인터페이스 전환: WLAN 사용
#         iface = USE_INTERFACE_WLAN
#         new_ip = get_ip_from_interface(iface)
#         print(f"[HO] Camera bind_ip={new_ip}, UDP iface={iface}")

#         # 스트림 목적지 라우트 wlan0으로 강제
#         route_replace_host(TARGET_TO_IP, iface)

#         # 카메라/UDP 경로 전환
#         camera.start(iface=iface, bind_ip=new_ip)
#         udpgen.update(iface=iface)

#     except Exception as e:
#         print(f"[HO] Error during handover: {e}")

def handover_ap(target_bssid):
    global last_handover_time, camera, udpgen
    try:
        print(f"[HO] Trying roam → {target_bssid}")
        subprocess.run(["sudo", "wpa_cli", "roam", target_bssid], check=True)
        subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bssid", target_bssid], check=True)
        subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bgscan", ""], check=True)

        # ✅ 연결 확인 루프
        success = False
        for i in range(10):  # 최대 10초 대기
            cur_bssid = get_current_bssid()
            if cur_bssid and cur_bssid.lower() == target_bssid.lower():
                success = True
                break
            time.sleep(1)

        if not success:
            print(f"❌ Handover to {target_bssid} failed (timeout)")
            return

        print(f"✅ Handover completed to {target_bssid}")
        last_handover_time = time.time()

        # ✅ IP 확인 대기 (DHCP 환경이면 더 길게 필요)
        new_ip = None
        for i in range(10):
            new_ip = get_ip_from_interface(USE_INTERFACE_WLAN)
            if new_ip and new_ip != "0.0.0.0":
                break
            time.sleep(1)

        if not new_ip:
            print(f"⚠️ Got BSSID {target_bssid}, but no IP on wlan0 yet")
            return

        print(f"[HO] Camera bind_ip={new_ip}, UDP iface={USE_INTERFACE_WLAN}")

        # 라우트 및 경로 전환
        route_replace_host(TARGET_TO_IP, USE_INTERFACE_WLAN)
        camera.start(iface=USE_INTERFACE_WLAN, bind_ip=new_ip)
        udpgen.update(iface=USE_INTERFACE_WLAN)

    except Exception as e:
        print(f"[HO] Error during handover: {e}")



# ----------- Socket.IO ----------------------------
sio = socketio.Client(
    reconnection=True,
    reconnection_attempts=0,
    reconnection_delay=1,
    reconnection_delay_max=5,
)

# Reconnect helper for socket.io
is_connecting = False
def reconnect_socket():
    global is_connecting
    if is_connecting:
        return False
    is_connecting = True
    try:
        for i in range(5):
            try:
                if sio.connected:
                    return True  # 이미 연결돼 있으면 끝
                sio.connect(SERVER_URL, auth={'robot_id': str(robot_id)})
                print("✅ Reconnected to server after handover.")
                return True
            except Exception as e:
                print(f"Reconnect attempt {i+1} failed: {e}")
                time.sleep(3)
        print("❌ Failed to reconnect after handover.")
        return False
    finally:
        is_connecting = False

def socketio_reconnect_watchdog():
    while True:
        if not sio.connected:
            print("[Watchdog] Socket.IO not connected. Trying to reconnect...")
            reconnect_socket()
            time.sleep(10)
        time.sleep(3)

@sio.event
def connect():
    print('✅ Connected to server')

@sio.event
def disconnect():
    print('❌ Disconnected from server')

@sio.event
def reboot(data):
    if data.get('robot_id') == str(robot_id):
        print(f"[CMD] 🔁 Reboot command received for robot_id={robot_id}")
        try:
            subprocess.run(["sudo", "reboot"])
        except Exception as e:
            print(f"[CMD] ⚠️ Failed to reboot: {e}")

@sio.event
def command(data):
    if data.get('robot_id') == str(robot_id):
        handover = data.get('handover')
        if handover is None:
            return

        try:
            handover_id = int(handover)
        except:
            print(f"[CMD] invalid handover value: {handover}")
            return

        if handover_id == 0:
            # 유선으로 복귀
            print(f"[{robot_id}] Handover ID is 0 → Use eth0, no Wi-Fi handover")
            iface = USE_INTERFACE_ETH
            local_ip = get_ip_from_interface(iface)
            # 스트림 목적지 라우트 eth0으로 강제
            route_replace_host(TARGET_TO_IP, iface)
            camera.start(iface=iface, bind_ip=local_ip)
            udpgen.update(iface=iface)
            return

        # Wi-Fi로 핸드오버
        target_bssid = AP_INFO.get(handover_id, {}).get('bssid', '').lower()
        if not target_bssid:
            print(f"[CMD] unknown handover id: {handover_id}")
            return

        print(f"[{robot_id}] Received handover request to BSSID: {target_bssid}")

        # if scan_lock.acquire(timeout=5):
        #     try:
        #         print(f"[{robot_id}] Starting handover_ap() ...")
        #         handover_ap(target_bssid)
        #     finally:
        #         scan_lock.release()
        # else:
        #     print(f"[{robot_id}] ⚠️ Skipped handover (scan_lock busy)")

        if scan_lock.acquire(timeout=5):
            try:
                print(f"[{robot_id}] Starting handover_ap() ...")
                handover_ap(target_bssid)
            finally:
                scan_lock.release()
        else:
            print(f"[{robot_id}] ⚠️ Scan loop busy, forcing handover anyway")
            # 락 못 잡아도 handover는 강제로 실행
            handover_ap(target_bssid)

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

            # if all(conn["connected"] == "false" for conn in connections):
                # time.sleep(1.0)
                # continue

            sensing_data = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data": {
                    "robot_id": robot_id,
                    "connections": connections
                }
            }
            print(json.dumps(sensing_data, indent=4))
            if sio.connected:
                sio.emit("robot_ss_data", sensing_data)
            else:
                print("[Sensing] Socket.IO not connected. Skipping emit.")
            time.sleep(10.0)
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
                subprocess.run(["sudo", "wpa_cli", "scan"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(10.0)
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

    # 1) Socket.IO 서버 IP는 항상 eth0로 라우팅 고정
    server_host = host_from_url(SERVER_URL)
    server_ip = resolve_host_to_ip(server_host) if server_host else ""
    if server_ip:
        route_replace_host(server_ip, USE_INTERFACE_ETH)

    # 2) 초기 스트림은 eth0 사용
    default_iface = USE_INTERFACE_ETH
    default_ip = get_ip_from_interface(default_iface)
    route_replace_host(TARGET_TO_IP, default_iface)
    camera.start(iface=default_iface, bind_ip=default_ip)
    udpgen.start()

    # watchdog 스레드는 1회만 시작
    # threading.Thread(target=socketio_reconnect_watchdog, daemon=True).start()
    threading.Thread(target=sensing_loop, daemon=True).start()
    threading.Thread(target=scan_loop, daemon=True).start()

    # try:
    #     # Socket.IO 연결 (eth0 경로로 나감: host route로 보장)
    #     sio.connect(SERVER_URL, auth={"robot_id": str(robot_id)})
    # except Exception as e:
    #     print(f"[SIO] initial connect failed: {e}")

    # ✅ 서버 연결 무한 재시도 (초기 연결 실패 대비)
    while not sio.connected:
        try:
            sio.connect(SERVER_URL, auth={"robot_id": str(robot_id)})
            print("✅ Connected to server")
        except Exception as e:
            print(f"[SIO] initial connect failed: {e}")
            time.sleep(5)

    while True:
        time.sleep(10)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    main()