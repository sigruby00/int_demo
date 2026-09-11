"""Optional USB gamepad control via pygame (SDL).

If a USB joystick is plugged in, its left stick drives the robot (same path as
the on-screen virtual joystick). If none is present, this thread simply idles and
retries, so the web joystick still works. Hot-plug is supported.
"""
import os
import threading
import time

# Import pygame at module load (main thread). Importing it inside a worker
# thread can deadlock on pygame's submodule locks. Headless: no video/audio.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
try:
    import pygame
    _PYGAME_OK = True
except Exception as _e:          # pragma: no cover
    pygame = None
    _PYGAME_OK = False
    print(f"[usb-joy] pygame unavailable, USB joystick disabled: {_e}")

MAX_LINEAR = 0.4        # m/s at full stick
MAX_ANGULAR = 1.2       # rad/s at full stick
DEADZONE = 0.12
POLL_HZ = 20


class USBJoystick:
    def __init__(self, control):
        self.control = control
        self._running = False
        self.connected = False
        self.name = None

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _open(self, pygame):
        pygame.joystick.quit()
        pygame.joystick.init()
        if pygame.joystick.get_count() == 0:
            self.connected = False
            self.name = None
            return None
        js = pygame.joystick.Joystick(0)
        js.init()
        self.connected = True
        self.name = js.get_name()
        print(f"[usb-joy] connected: {self.name}")
        return js

    def _loop(self):
        if not _PYGAME_OK:
            return
        try:
            pygame.display.init()     # needed for the event pump (dummy driver)
            pygame.joystick.init()
        except Exception as e:
            print(f"[usb-joy] init failed, USB joystick disabled: {e}")
            return

        js = None
        last_active = False
        period = 1.0 / POLL_HZ
        while self._running:
            try:
                pygame.event.pump()
                if js is None or pygame.joystick.get_count() == 0:
                    if self.connected:
                        print("[usb-joy] disconnected")
                    js = self._open(pygame)
                    if js is None:
                        time.sleep(1.5)
                        continue

                ax_lr = js.get_axis(0)          # left stick X -> turn
                ax_ud = js.get_axis(1)          # left stick Y -> forward
                lin = 0.0 if abs(ax_ud) < DEADZONE else -ax_ud * MAX_LINEAR
                ang = 0.0 if abs(ax_lr) < DEADZONE else -ax_lr * MAX_ANGULAR

                active = lin != 0.0 or ang != 0.0
                if active:
                    self.control.drive(lin, ang)
                    last_active = True
                elif last_active:
                    self.control.stop()          # send one stop on release
                    last_active = False
            except Exception as e:
                print(f"[usb-joy] loop error: {e}")
                js = None
                self.connected = False
                time.sleep(1.0)
            time.sleep(period)

    def status(self):
        return {"connected": self.connected, "name": self.name}

    def stop(self):
        self._running = False
