"""Network interface status (eth0 / wlan0 / wlan1) via NetworkManager.

Reports, per interface: state, connection name, SSID (wifi), IPv4 address, and
whether the address is DHCP or Static.
"""
import subprocess

IFACES = ["eth0", "wlan0", "wlan1"]
_METHOD = {"auto": "DHCP", "manual": "Static", "link-local": "Link-local",
           "shared": "Shared", "disabled": "Disabled"}


def _nmcli(*args):
    try:
        return subprocess.check_output(["nmcli", "-t", *args], text=True,
                                       stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def _dev_show(dev):
    """Parse `nmcli -t device show <dev>` into a dict (last value per key)."""
    d = {}
    for line in _nmcli("-f",
                        "GENERAL.TYPE,GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY",
                        "device", "show", dev).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    return d


def interface(dev):
    d = _dev_show(dev)
    con = d.get("GENERAL.CONNECTION", "") or ""
    info = {
        "name": dev,
        "type": "wifi" if dev.startswith("wlan") else "ethernet",
        "state": (d.get("GENERAL.STATE", "") or "").split(" ", 1)[-1] or "unknown",
        "connection": con if con and con != "--" else None,
        "ssid": None,
        "ip": (d.get("IP4.ADDRESS[1]") or None),
        "gateway": (d.get("IP4.GATEWAY") or None),
        "method": None,
    }
    if info["connection"]:
        method = _nmcli("-g", "ipv4.method", "connection", "show",
                        info["connection"]).strip()
        info["method"] = _METHOD.get(method, method or None)
        if info["type"] == "wifi":
            ssid = _nmcli("-g", "802-11-wireless.ssid", "connection", "show",
                          info["connection"]).strip()
            info["ssid"] = ssid or None
    return info


def all_interfaces():
    return [interface(d) for d in IFACES]
