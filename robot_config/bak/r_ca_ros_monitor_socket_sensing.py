import os
import sys
import socket  # Importing socket module again
from datetime import datetime
import random
import time
import statistics
import threading

# 현재 스크립트 기준 상대 경로에 설정 파일이 있다고 가정
sys.path.append(os.path.dirname(__file__))
from config import ca_id, to_id, AP_INFO

import json
import socketio  # pip install "python-socketio[client]"
import subprocess

# Socket.IO 클라이언트 생성
robot_id = ca_id

SERVER_URL = 'http://10.243.76.27:6789'

scan_lock = threading.Lock()
last_handover_time = 0

# RSSI moving average 저장용
rssi_history = {}  # bssid: [rssi1, rssi2, ...]
MOVING_AVG_N = 4


"""
Socket.IO Connectors
"""
sio = socketio.Client(
    reconnection=True,
    reconnection_attempts=0,
    reconnection_delay=0.1,
    reconnection_delay_max=0.5,
)

def reconnect_socket():
    for i in range(5):
        try:
            if sio.connected:
                sio.disconnect()
            time.sleep(0.5)
            sio.connect(SERVER_URL, auth={'robot_id': str(robot_id)})
            print("✅ Reconnected to server after handover.")
            return True
        except Exception as e:
            print(f"Reconnect attempt {i+1} failed: {e}")
            time.sleep(2)
    print("❌ Failed to reconnect after handover.")
    return False

def socketio_reconnect_watchdog():
    while True:
        if not sio.connected:
            print("[Watchdog] Socket.IO not connected. Trying to reconnect...")
            reconnect_socket()
        time.sleep(0.5)

@sio.event
def connect():
    print('Connected to server.')

@sio.event
def disconnect():
    print('Disconnected from server.')

@sio.event
def command(data):
    print(data)
    if data.get('robot_id') == str(robot_id):
        handover = data.get('handover')
        if handover:
            handover_id = int(handover, 0)
            target_bssid = AP_INFO[handover_id]['bssid'].lower()
            print(f"[{robot_id}] Received handover request to BSSID: {target_bssid}")

            current_bssid = get_current_bssid()
            if current_bssid == target_bssid:
                print(f"[{robot_id}] Already connected to BSSID {current_bssid}. Skipping handover.")
                return

            # Try to acquire lock for handover
            acquired = scan_lock.acquire(timeout=5)
            if not acquired:
                print("Timeout: Unable to acquire scan lock for handover")
                return

            try:
                print(f"Performing handover to AP {handover_id}")
                handover_ap(target_bssid)
            finally:
                scan_lock.release()


# skip if all connections are false
def sensing_loop():
    gateway_list = list(AP_INFO.keys())
    while True:
        try:
            cur_bssid = get_current_bssid()
            cur_ap_id = get_ap_id_from_bssid(cur_bssid)
            rssi_map = get_rssi_map_from_scan_results()
            time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            connections = [
                {
                    "gateway_id": gw_id,
                    "mac_address": AP_INFO[gw_id]['bssid'],
                    "connected": str(gw_id == cur_ap_id).lower(),
                    "rssi": rssi_map.get(AP_INFO[gw_id]['bssid'].lower(), -100),
                }
                for gw_id in gateway_list
            ]

            # 모든 connected가 'false'이면 skip
            if all(conn["connected"] == "false" for conn in connections):
                print("⚠️ All connections are false — skipping emit")
                time.sleep(1.0)
                continue

            sensing_data = {
                "timestamp": time_now,
                "data": {
                    "robot_id": robot_id,
                    "connections": connections
                }
            }
            sensing_data = json.dumps(sensing_data).encode()
            print(sensing_data)
            # sio.emit('robot_ss_data', sensing_data)
            time.sleep(1.0)
        except Exception as e:
            print(f"Error in sensing loop: {e}")
            time.sleep(1)


def get_current_bssid():
    try:
        output = subprocess.check_output(["sudo", "wpa_cli", "status"], text=True)
        for line in output.splitlines():
            if line.startswith("bssid="):
                return line.split("=", 1)[1].strip().lower()
    except subprocess.CalledProcessError as e:
        print(f"Failed to get BSSID via wpa_cli: {e}")
    return None


def get_ap_id_from_bssid(bssid):
    try:
        for ap in AP_INFO.values():
            if ap['bssid'].lower() == bssid.lower():
                return ap['ap_id']
    except:
        print(f"Error getting AP ID for BSSID {bssid}")
        return None
    return None


def get_rssi_map_from_scan_results():
    global rssi_history
    try:
        output = subprocess.check_output(["sudo", "wpa_cli", "scan_results"], text=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to get scan results: {e}")
        return {}

    rssi_map = {}
    lines = output.splitlines()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 5:
            bssid, freq, signal, flags, ssid = parts[0], parts[1], parts[2], parts[3], " ".join(parts[4:])
            if ssid == "HSLSV":
                try:
                    rssi_val = float(signal)
                    # moving average 적용
                    if bssid not in rssi_history:
                        rssi_history[bssid] = []
                    rssi_history[bssid].append(rssi_val)
                    if len(rssi_history[bssid]) > MOVING_AVG_N:
                        rssi_history[bssid].pop(0)
                    if len(rssi_history[bssid]) < MOVING_AVG_N:
                        avg_rssi = rssi_history[bssid][-1]
                    else:
                        avg_rssi = sum(rssi_history[bssid]) / MOVING_AVG_N
                    rssi_map[bssid.lower()] = avg_rssi
                except ValueError:
                    continue
    return rssi_map


def lock_bssid(bssid):
    try:
        subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bssid", bssid], check=True)
        subprocess.run(["sudo", "wpa_cli", "set_network", "0", "bgscan", ""], check=True)
        print(f"[LOCK] BSSID locked to {bssid} and bgscan disabled")
    except subprocess.CalledProcessError as e:
        print(f"[LOCK] Failed to lock BSSID or disable bgscan: {e}")


def handover_ap(target_bssid):
    global last_handover_time
    try:
        subprocess.run(["sudo", "wpa_cli", "roam", target_bssid], check=True)
        lock_bssid(target_bssid)
        print(f"Successfully handed over to BSSID: {target_bssid}")
        last_handover_time = time.time()

        # BSSID 전환 확인
        for _ in range(10):
            bssid = get_current_bssid()
            if bssid and bssid == target_bssid:
                print(f"Confirmed BSSID after roam: {bssid}")
                break
            print("Waiting for BSSID confirmation...")
            time.sleep(0.5)
        else:
            print(f"Warning: BSSID {target_bssid} not confirmed after roam.")

        # 네트워크 연결 확인 (고정 IP 환경)
        for _ in range(5):
            try:
                subprocess.check_output(["ping", "-c", "1", "-W", "1", "10.243.76.1"], stderr=subprocess.DEVNULL)
                print("Network connectivity confirmed after roam.")
                break
            except subprocess.CalledProcessError:
                print("Waiting for network availability...")
                time.sleep(0.5)

        # socket.io 강제 reconnect (혼합 방식: disconnect → connect)
        try:
            if sio.connected:
                sio.disconnect()
            time.sleep(0.2)
            sio.connect(SERVER_URL, auth={'robot_id': str(robot_id)})
            print("✅ Force-handshake reconnected.")
        except Exception as e:
            print(f"[ERROR] Force-handshake failed: {e}")

    except subprocess.CalledProcessError as e:
        print(f"Error during handover: {e}")
    except Exception as e:
        print(f"Unexpected error in handover_ap: {e}")


def scan_loop():
    global last_handover_time
    while True:
        if time.time() - last_handover_time < 3:
            time.sleep(1)
            continue
        if scan_lock.acquire(blocking=False):
            try:
                # print("[SCAN] Starting scan")
                subprocess.run(["sudo", "wpa_cli", "scan", "freq", "5180", "5190", "5200", "5210", "5220", "5230", "5240"], check=True)
                time.sleep(2.0)
            except subprocess.CalledProcessError as e:
                print(f"Scan error: {e}")
            finally:
                scan_lock.release()
                # print("[SCAN] Scan complete")
        time.sleep(1.0)

def main():
    import threading
    threading.Thread(target=sensing_loop, daemon=True).start()
    threading.Thread(target=scan_loop, daemon=True).start()
    threading.Thread(target=socketio_reconnect_watchdog, daemon=True).start()

    # socket.io 서버 연결
    try:
        # sio.connect(SERVER_URL)
        sio.connect(SERVER_URL, auth={'robot_id': str(robot_id)}) # handshake with robot_id
    except Exception as e:
        print(f"Failed to connect to server: {e}")
        return

    while True:
        try:
            time.sleep(10)
        except KeyboardInterrupt:
            print("KeyboardInterrupt detected. Exiting...")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")

    print("ros_monitor_socket_sensing.py Closed")

if __name__ == '__main__':
    main()