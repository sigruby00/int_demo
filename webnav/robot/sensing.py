"""Local Wi-Fi base-station sensing.

Scans nearby APs with wpa_cli on the sense interface and reports the (smoothed)
RSSI for each BS in the configured list. No data leaves the robot -- results are
printed locally and exposed to the web UI.
"""
import subprocess
import threading
import time

from config import settings


class Sensing:
    def __init__(self, bridge=None):
        self.bridge = bridge
        self.iface = settings.WLAN_SENSE_IFACE
        self._rssi_history = {}
        self._ssid_by_bssid = {}
        self._latest = {"timestamp": None, "connected_bssid": None,
                        "connected_ap_id": None, "connections": []}
        self._lock = threading.Lock()
        self._running = False

    # ---- low-level wpa_cli helpers ---------------------------------------
    def _wpa(self, *args):
        try:
            return subprocess.check_output(
                ["sudo", "wpa_cli", "-i", self.iface, *args],
                text=True, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            return ""

    def current_bssid(self):
        for line in self._wpa("status").splitlines():
            if line.startswith("bssid="):
                return line.split("=", 1)[1].strip().lower()
        return None

    def connected_rssi(self):
        for line in self._wpa("signal_poll").splitlines():
            if line.startswith("RSSI="):
                try:
                    return float(line.split("=", 1)[1].strip())
                except ValueError:
                    return None
        return None

    def _smooth(self, bssid, value):
        hist = self._rssi_history.setdefault(bssid, [])
        hist.append(value)
        if len(hist) > settings.MOVING_AVG_N:
            hist.pop(0)
        return sum(hist) / len(hist)

    def rssi_map(self):
        """{bssid_lower: smoothed_rssi} from the latest scan results.

        Also records each BSSID's SSID (scan_results is tab-separated:
        bssid / frequency / signal / flags / ssid)."""
        out = {}
        lines = self._wpa("scan_results").splitlines()
        for line in lines[1:]:                       # skip header row
            parts = line.split("\t") if "\t" in line else line.split()
            if len(parts) >= 3:
                bssid = parts[0].lower()
                if len(parts) >= 5:
                    self._ssid_by_bssid[bssid] = parts[4].strip()
                try:
                    out[bssid] = self._smooth(bssid, float(parts[2]))
                except ValueError:
                    continue
        # override connected AP with a fresh signal_poll reading
        cur = self.current_bssid()
        if cur:
            live = self.connected_rssi()
            if live is not None:
                out[cur] = self._smooth(cur, live)
        return out

    def request_scan(self):
        self._wpa("scan")

    # ---- handover (roam wlan1 to a chosen BSSID) --------------------------
    def _wlan_connection(self):
        """NetworkManager connection name active on the sense interface."""
        try:
            out = subprocess.check_output(
                ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "device", "show", self.iface],
                text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                if line.startswith("GENERAL.CONNECTION:"):
                    c = line.split(":", 1)[1].strip()
                    return c if c and c != "--" else None
        except Exception:
            pass
        return None

    def handover(self, bssid):
        """Roam wlan1 to a specific BSSID (persistent NM lock + fast reassoc)."""
        bssid = bssid.strip().lower()
        con = self._wlan_connection()
        if con:
            subprocess.run(["sudo", "-n", "nmcli", "connection", "modify", con,
                            "wifi.bssid", bssid], check=False, capture_output=True)
        self._wpa("set_network", "0", "bssid", bssid)
        self._wpa("reassociate")
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if self.current_bssid() == bssid:
                return True, bssid
            time.sleep(0.1)
        return (self.current_bssid() == bssid), self.current_bssid()

    def roam_status(self):
        """Whether wlan1 is BSSID-locked (handover) or free to auto-roam."""
        con = self._wlan_connection()
        locked_bssid = None
        if con:
            try:
                out = subprocess.check_output(
                    ["nmcli", "-g", "802-11-wireless.bssid", "connection", "show", con],
                    text=True, stderr=subprocess.DEVNULL).strip()
                locked_bssid = out.replace("\\", "").lower() if out else None
            except Exception:
                pass
        return {"auto_roam": locked_bssid is None,
                "locked_bssid": locked_bssid}

    def unlock_roam(self):
        """Clear the BSSID lock so wpa_supplicant may auto-roam again."""
        con = self._wlan_connection()
        if con:
            subprocess.run(["sudo", "-n", "nmcli", "connection", "modify", con,
                            "wifi.bssid", ""], check=False, capture_output=True)
        self._wpa("set_network", "0", "bssid", "any")
        self._wpa("reassociate")
        return True

    # ---- one sensing snapshot --------------------------------------------
    def sample(self):
        bs_list = settings.load_bs_list()
        cur_bssid = self.current_bssid()
        cur_ap_id = next((b["ap_id"] for b in bs_list
                          if b["bssid"].lower() == (cur_bssid or "")), None)
        rmap = self.rssi_map()
        connections = [{
            "ap_id": b["ap_id"],
            "bssid": b["bssid"],
            "ssid": self._ssid_by_bssid.get(b["bssid"].lower()),
            "connected": (b["ap_id"] == cur_ap_id),
            "rssi": round(rmap.get(b["bssid"].lower(), -100), 1),
        } for b in bs_list]

        state = self.bridge.get_state() if self.bridge else {}
        snap = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "robot_id": settings.ROBOT_ID,
            "connected_bssid": cur_bssid,
            "connected_ap_id": cur_ap_id,
            "pose": {"x": round(state.get("x", 0.0), 3),
                     "y": round(state.get("y", 0.0), 3),
                     "yaw": round(state.get("yaw", 0.0), 3)},
            "battery": state.get("battery"),
            "connections": connections,
        }
        with self._lock:
            self._latest = snap
        return snap

    def latest(self):
        with self._lock:
            return dict(self._latest)

    # ---- background loops -------------------------------------------------
    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._sense_loop, daemon=True).start()
        threading.Thread(target=self._scan_loop, daemon=True).start()

    def _sense_loop(self):
        import json
        while self._running:
            try:
                snap = self.sample()
                print(json.dumps(snap))          # local print (visible in tmux)
            except Exception as e:
                print(f"[sensing] error: {e}")
            time.sleep(settings.SENSE_PERIOD_S)

    def _scan_loop(self):
        while self._running:
            try:
                self.request_scan()
            except Exception as e:
                print(f"[sensing] scan error: {e}")
            time.sleep(settings.SCAN_PERIOD_S)

    def stop(self):
        self._running = False
