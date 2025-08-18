#!/usr/bin/env python3
import socket
import time
from config import to_id

TO_IP_LIST = [
    {"to_id": 0, "to_ip": "192.168.11.31"},
    {"to_id": 1, "to_ip": "10.100.30.21"},
    {"to_id": 2, "to_ip": "10.100.30.22"},
    {"to_id": 3, "to_ip": "10.100.30.23"},
    {"to_id": 4, "to_ip": "10.100.30.24"},
    {"to_id": 5, "to_ip": "10.100.30.25"},
    {"to_id": 6, "to_ip": "10.100.30.26"},
    {"to_id": 7, "to_ip": "10.100.30.27"},
    {"to_id": 8, "to_ip": "10.100.30.28"}
]

def udp_client(server_ip, server_port=5001, bitrate_mbps=15, duration=10, packet_size=1400):
    """
    간단한 UDP 클라이언트 (iperf -c -u -b 15M 비슷)
    server_ip: 수신 서버 주소
    server_port: 수신 서버 포트
    bitrate_mbps: 전송 속도 (Mbps)
    duration: 전송 시간 (초)
    packet_size: 패킷 크기 (바이트)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    target_bps = bitrate_mbps * 1_000_000  # Mbps → bps
    target_Bps = target_bps / 8            # bytes per second
    pps = target_Bps / packet_size         # packets per second
    interval = 1.0 / pps                   # 패킷 사이 간격 (초)

    print(f"Sending to {server_ip}:{server_port} at {bitrate_mbps} Mbps "
          f"({pps:.0f} packets/sec, packet={packet_size} bytes, duration={duration}s)")

    payload = b'a' * packet_size
    sent_bytes = 0
    start = time.time()

    while time.time() - start < duration:
        sock.sendto(payload, (server_ip, server_port))
        sent_bytes += len(payload)
        time.sleep(interval)

    elapsed = time.time() - start
    rate_mbps = (sent_bytes * 8) / (elapsed * 1e6)
    print(f"Done. Sent {sent_bytes} bytes in {elapsed:.2f} sec "
          f"({rate_mbps:.2f} Mbps).")

    sock.close()


if __name__ == "__main__":
    # 예시: 192.168.11.31 서버로 15 Mbps, 10초 동안 전송
    ip = next((item['to_ip'] for item in TO_IP_LIST if item['to_id'] == to_id), None)
    if ip is None:
        raise ValueError(f"Invalid to_id: {to_id}")
    udp_client(ip, server_port=5001, bitrate_mbps=15, duration=10)