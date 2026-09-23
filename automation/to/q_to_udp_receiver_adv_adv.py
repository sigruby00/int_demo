#!/usr/bin/env python3
import threading
import socket
import psutil
import time
import os
import math
import struct
import socketio  # pip install "python-socketio[client]"
from datetime import datetime
import config as cfg

SERVER_URL = "http://10.100.30.241:6789"
# SERVER_URL = "https://6b08ef0ec81e.ngrok.app" # ngrok

# to_id = 0 #same as ca_id
to_id = cfg.to_id

# ✅ 전역 변수
latest_delay = 0.0
latest_jitter = 0.0

# ──────────────────────────────────────────────
# Socket.IO 클라이언트 설정
sio = socketio.Client(
    reconnection=True,
    reconnection_attempts=0,
    reconnection_delay=0.1,
    reconnection_delay_max=0.5,
)

# ──────────────────────────────────────────────
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
                sio.connect(
                    SERVER_URL,
                    auth={
                        "type": "to",
                        "id": str(to_id),
                    },
                )
                print("✅ Reconnected to server after handover.")
                return True
            except Exception as e:
                print(f"Reconnect attempt {i+1} failed: {e}")
                # python-socketio can get stuck "not in a disconnected state" after the
                # server restarts: force a clean disconnect before the next attempt
                try:
                    sio.disconnect()
                except Exception:
                    pass
                time.sleep(3)
        print("❌ Failed to reconnect after handover -> exiting so the runner restarts a clean process")
        os._exit(1)
    finally:
        is_connecting = False


def socketio_reconnect_watchdog():
    while True:
        if not sio.connected:
            print("[Watchdog] Socket.IO not connected. Trying to reconnect...")
            reconnect_socket()
            time.sleep(10)  # 재시도 간격 늘려줌
        time.sleep(3)


# ──────────────────────────────────────────────
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


# ──────────────────────────────────────────────
def udp_server(host="0.0.0.0", port=5001, buffer_size=65535, avg_interval=1):
    """
    간단한 UDP 서버
    - per-packet 지연 계산
    - 일정 주기(avg_interval)마다 평균 delay/jitter 계산 및 전역 변수 갱신
    """
    global latest_delay, latest_jitter  # ✅ 전역 변수 갱신을 위해 필요

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, port))
        prev_delay = None
        jitter = 0

        # interval average를 위한 버퍼
        delay_buffer = []
        jitter_buffer = []
        last_report_time = time.time()

        print(f"UDP server listening on {host}:{port}")
        while True:
            data, addr = sock.recvfrom(buffer_size)
            recv_time = time.time()
            if len(data) >= 8:
                try:
                    send_time = struct.unpack("!d", data[:8])[0]
                    delay = recv_time - send_time
                    if abs(send_time - recv_time) > 10:
                        continue

                    if not math.isnan(delay) and not math.isinf(delay):
                        if prev_delay is not None:
                            delta = abs(delay - prev_delay)
                            if not math.isnan(delta):
                                jitter += (delta - jitter) / 16
                        prev_delay = delay

                        # 버퍼에 저장
                        delay_buffer.append(delay)
                        jitter_buffer.append(jitter)

                    # 주기 평균 계산
                    if time.time() - last_report_time >= avg_interval:
                        if delay_buffer:
                            avg_delay = sum(delay_buffer) / len(delay_buffer)
                        else:
                            avg_delay = 0.0
                        if jitter_buffer:
                            avg_jitter = sum(jitter_buffer) / len(jitter_buffer)
                        else:
                            avg_jitter = 0.0

                        latest_delay = avg_delay
                        latest_jitter = avg_jitter

                        print(
                            f"[UDP AVG] Delay: {avg_delay * 1000:.3f} ms | Jitter: {avg_jitter * 1000:.3f} ms"
                        )

                        # 버퍼 초기화
                        delay_buffer.clear()
                        jitter_buffer.clear()
                        last_report_time = time.time()

                except Exception as e:
                    print(f"[UDP Error] {e}")
    except KeyboardInterrupt:
        print("\nServer stopped by user.")
    finally:
        sock.close()


# ──────────────────────────────────────────────
@sio.event
def connect():
    print("Connected to server.")


@sio.event
def disconnect():
    print("Disconnected from server.")


# ──────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=socketio_reconnect_watchdog, daemon=True).start()
    threading.Thread(target=udp_server, kwargs={"port": 6001}, daemon=True).start()

    iface = "enp1s0"  # 모니터링할 인터페이스 (None이면 전체)
    interval = 1  # throughput 측정 주기(초) — 1 s fleet-wide cadence (2026-09-23)

    while True:
        recv, sent = get_throughput(interval, iface)

        # ✅ 전역 변수에서 최신 평균 delay/jitter 값 읽기
        delay_ms = latest_delay * 1000
        jitter_ms = latest_jitter * 1000

        print(
            f"[{iface}] Incoming: {recv:.2f} Mbps | Outgoing: {sent:.2f} Mbps | Delay(avg): {delay_ms:.3f} ms | Jitter(avg): {jitter_ms:.3f} ms"
        )

        time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pf_data = {
            "timestamp": time_now,
            "data": {
                "ca_id": to_id,
                "throughput": recv,
                "delay": delay_ms,
                "jitter": jitter_ms,
            },
        }
        print(pf_data)

        if sio.connected:
            sio.emit("robot_pf_data", pf_data)
