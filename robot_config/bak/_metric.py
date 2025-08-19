#!/usr/bin/env python3
import psutil
import time

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


if __name__ == "__main__":
    iface = "eth0"  # 모니터링할 인터페이스 (None이면 전체)
    interval = 5    # 5초 평균

    while True:
        recv, sent = get_throughput(interval, iface)
        print(f"[{iface}] Incoming: {recv:.2f} Mbps | Outgoing: {sent:.2f} Mbps")