#!/usr/bin/env python3
import socket
import time

def udp_server(host="0.0.0.0", port=5001, buffer_size=65535):
    """
    간단한 UDP 서버 (iperf -s -u 와 유사)
    클라이언트가 여러 번 실행되어도 매번 수신 가능.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    print(f"UDP server listening on {host}:{port}")

    total_bytes = 0
    start_time = time.time()

    try:
        while True:
            data, addr = sock.recvfrom(buffer_size)
            total_bytes += len(data)
            elapsed = time.time() - start_time
            if elapsed > 0:
                rate_mbps = (total_bytes * 8) / (elapsed * 1e6)
            else:
                rate_mbps = 0

            print(f"[{addr}] {len(data)} bytes received "
                  f"(total={total_bytes} bytes, {rate_mbps:.2f} Mbps)")

    except KeyboardInterrupt:
        print("\nServer stopped by user.")
    finally:
        sock.close()

if __name__ == "__main__":
    udp_server(port=5001)