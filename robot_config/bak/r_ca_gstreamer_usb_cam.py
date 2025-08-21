#!/usr/bin/env python3
import subprocess
import signal
import config as cfg

TO_IP_LIST = [
    {"to_id": 2, "to_ip": "10.100.30.21"},
    {"to_id": 3, "to_ip": "10.100.30.22"},
    {"to_id": 4, "to_ip": "10.100.30.23"},
    {"to_id": 5, "to_ip": "10.100.30.24"},
    {"to_id": 6, "to_ip": "10.100.30.25"},
    {"to_id": 7, "to_ip": "10.100.30.26"},
    {"to_id": 8, "to_ip": "10.100.30.27"},
    {"to_id": 9, "to_ip": "10.100.30.28"}
]


def start_gstreamer(to_id):
    to_ip = next((item['to_ip'] for item in TO_IP_LIST if item['to_id'] == to_id), None)
    if to_ip is None:
        raise ValueError(f"Invalid to_id: {to_id}")

    gst_cmd = [
        'gst-launch-1.0',
        'v4l2src', 'device=/dev/video2',
        '!', 'video/x-h264,width=1920,height=1080,framerate=30/1',
        '!', 'rtph264pay', 'config-interval=1', 'pt=96',
        '!', 'udpsink', f'host={to_ip}', 'port=5000'
    ]

    return subprocess.Popen(gst_cmd, stdin=subprocess.PIPE)

def main():
    to_id = 2
    gst_proc = start_gstreamer(to_id)

    try:
        gst_proc.wait()

    except KeyboardInterrupt:
        gst_proc.send_signal(signal.SIGINT)
        gst_proc.wait()

if __name__ == '__main__':
    main()
