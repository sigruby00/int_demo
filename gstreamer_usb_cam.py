#!/usr/bin/env python3
import cv2
import subprocess
import signal

def get_bitrate(width, height):
    if width >= 3840 or height >= 2160:
        return 10000
    elif width >= 2560 or height >= 1440:
        return 6000
    elif width >= 1920 or height >= 1080:
        return 4500
    elif width >= 1280 or height >= 720:
        return 2500
    elif width >= 640 or height >= 480:
        return 1000
    else:
        return 800  # fallback for very low resolution

def start_gstreamer(width, height, bitrate):
    gst_cmd = [
        'gst-launch-1.0', 'fdsrc', '!',
        'videoparse', f'width={width}', f'height={height}', 'format=rgb', 'framerate=30/1', '!',
        'videoconvert', '!',
        'x264enc', 'tune=zerolatency', f'bitrate={bitrate}', 'speed-preset=ultrafast', '!',
        'rtph264pay', 'config-interval=1', 'pt=96', '!',
        'udpsink', 'host=192.168.11.100', 'port=5000'
    ]
    return subprocess.Popen(gst_cmd, stdin=subprocess.PIPE)

def main():
    cap = cv2.VideoCapture('/dev/video0', cv2.CAP_V4L2)

    # 원하는 해상도 설정
    width = 1920
    height = 1080
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, 30)

    # 비트레이트 자동 설정
    bitrate = get_bitrate(width, height)
    print(f"Using bitrate: {bitrate} kbps for resolution {width}x{height}")

    gst_process = start_gstreamer(width, height, bitrate)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Camera read failed.")
                break

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            gst_process.stdin.write(rgb_frame.tobytes())
            gst_process.stdin.flush()  # 안정성 향상
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        gst_process.send_signal(signal.SIGINT)
        gst_process.wait()

if __name__ == '__main__':
    main()