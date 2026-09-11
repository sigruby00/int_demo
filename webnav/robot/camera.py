"""Local camera preview (MJPEG) for the web UI. Nothing is streamed off-robot.

Frames are captured with OpenCV and served as a multipart MJPEG stream by the
web server. If the camera can't be opened, the stream endpoint just yields
nothing and the rest of the UI keeps working.
"""
import threading
import time

from config import settings


class Camera:
    def __init__(self):
        self._cap = None
        self._frame = None
        self._lock = threading.Lock()
        self._running = False
        self.available = False

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        try:
            import cv2
        except Exception as e:
            print(f"[camera] opencv unavailable: {e}")
            return
        while self._running:
            if self._cap is None:
                self._cap = cv2.VideoCapture(settings.CAMERA_DEVICE)
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.CAMERA_WIDTH)
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.CAMERA_HEIGHT)
                if not self._cap.isOpened():
                    self.available = False
                    self._cap = None
                    time.sleep(2)
                    continue
                self.available = True
            ok, frame = self._cap.read()
            if not ok:
                self.available = False
                self._cap.release()
                self._cap = None
                time.sleep(1)
                continue
            ok, jpg = cv2.imencode(".jpg", frame,
                                   [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                with self._lock:
                    self._frame = jpg.tobytes()
            time.sleep(1.0 / max(1, settings.CAMERA_FPS))

    def mjpeg_generator(self):
        boundary = b"--frame"
        while True:
            with self._lock:
                frame = self._frame
            if frame is not None:
                yield (boundary + b"\r\nContent-Type: image/jpeg\r\n\r\n"
                       + frame + b"\r\n")
            time.sleep(1.0 / max(1, settings.CAMERA_FPS))

    def stop(self):
        self._running = False
        if self._cap is not None:
            self._cap.release()
