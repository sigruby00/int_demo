#!/usr/bin/env python3
import threading
import socket
import time
import socketio  # pip install "python-socketio[client]"

SERVER_URL = 'http://10.100.30.241:6789'
# SERVER_URL = 'http://10.100.30.241:6789' #ngrok ip

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

            print(f"[{addr}] {len(data)} bytes received "
                  f"(total={total_bytes} bytes, {rate_mbps:.2f} Mbps)")

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