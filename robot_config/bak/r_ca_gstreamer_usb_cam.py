#!/usr/bin/env python3
import subprocess
import signal
import config as cfg

def start_gstreamer(to_id):
    to_ip = next((item['to_ip'] for item in cfg.TO_IP_LIST if item['to_id'] == to_id), None)
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
    to_id = cfg.to_id
    gst_proc = start_gstreamer(to_id)

    try:
        gst_proc.wait()

    except KeyboardInterrupt:
        gst_proc.send_signal(signal.SIGINT)
        gst_proc.wait()

if __name__ == '__main__':
    main()
