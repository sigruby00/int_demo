#!/usr/bin/env python3
import threading
import socket
import psutil
import time
import socketio  # pip install "python-socketio[client]"
from datetime import datetime

# SERVER_URL = 'http://10.100.30.241:6789'
SERVER_URL = "https://6b08ef0ec81e.ngrok.app" # ngrok

to_id = 0 #same as ca_id

sio = socketio.Client(
    reconnection=True,
    reconnection_attempts=0,
    reconnection_delay=0.1,
    reconnection_delay_max=0.5,
)

# Reconnect helper for socket.io
def reconnect_socket():
    for i in range(5):
        try:
            if sio.connected:
                sio.disconnect()
            time.sleep(0.5)
            sio.connect(SERVER_URL, auth={'to_id': str(to_id)})
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

def get_throughput(interval=5, iface=None):
    """
    interval: 측정 주기 (초)
    iface: 특정 인터페이스 지정 (예: "eth0"), None이면 전체 합산
    """
    # 첫 번째 측정
    net1 = psutil.net_io_counters(pernic=True if iface else False)
    if iface:
        net1 = net1[iface]
    bytes_recv1 = net1.bytes_recv
    bytes_sent1 = net1.bytes_sent

    time.sleep(interval)

    # 두 번째 측정
    net2 = psutil.net_io_counters(pernic=True if iface else False)
    if iface:
        net2 = net2[iface]
    bytes_recv2 = net2.bytes_recv
    bytes_sent2 = net2.bytes_sent

    # 전송량 차이
    delta_recv = bytes_recv2 - bytes_recv1
    delta_sent = bytes_sent2 - bytes_sent1

    # 초당 byte → Mbps 변환
    recv_mbps = (delta_recv * 8) / (interval * 1e6)
    sent_mbps = (delta_sent * 8) / (interval * 1e6)

    return recv_mbps, sent_mbps

def udp_server(host="0.0.0.0", port=5001, buffer_size=65535):
    """
    간단한 UDP 서버 (iperf -s -u 와 유사)
    클라이언트가 여러 번 실행되어도 매번 수신 가능.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, port))
        print(f"UDP server listening on {host}:{port}")
        while True:
            data, addr = sock.recvfrom(buffer_size)
            # 필요하면 처리 로직 추가
    except KeyboardInterrupt:
        print("\nServer stopped by user.")
    finally:
        sock.close()

@sio.event
def connect():
    print('Connected to server.')

@sio.event
def disconnect():
    print('Disconnected from server.')

if __name__ == "__main__":

    threading.Thread(target=socketio_reconnect_watchdog, daemon=True).start()
    udp_server(port=5001)

    iface = "eth0"  # 모니터링할 인터페이스 (None이면 전체)
    interval = 5    # 5초 평균

    while True:
        recv, sent = get_throughput(interval, iface)
        print(f"[{iface}] Incoming: {recv:.2f} Mbps | Outgoing: {sent:.2f} Mbps")

        time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pf_data = {
            "timestamp": time_now,
            "data": {
                "ca_id": to_id,
                "throughput": rate_mbps
            }
        }
        print(pf_data)

        if sio.connected:
            sio.emit('robot_pf_data', pf_data)