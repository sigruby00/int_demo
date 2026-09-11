"""Network (re)configuration for wlan0 / wlan1 via NetworkManager.

DANGER: reconfiguring the interface you are currently connected through drops
your own link. These operations are therefore gated to clients arriving over
eth0 (a separate, stable link) -- see `client_via_eth()`. Plug in ethernet,
open the web UI on the eth0 IP, then change the Wi-Fi interfaces safely.
"""
import ipaddress
import subprocess

from robot import netinfo


def _nmcli(*args, timeout=25):
    try:
        return subprocess.run(["sudo", "-n", "nmcli", *args], text=True,
                              capture_output=True, timeout=timeout)
    except Exception as e:
        class _R:                       # mimic CompletedProcess on failure
            returncode = 1
            stdout = ""
            stderr = str(e)
        return _R()


# ---- eth-only safety gate -------------------------------------------------
def client_via_eth(remote_addr):
    """True if the request's source IP is on eth0's subnet (i.e. the client is
    connected over ethernet), so reconfiguring wlan won't cut them off."""
    eth = netinfo.interface("eth0")
    if not eth.get("ip"):
        return False
    try:
        net = ipaddress.ip_interface(eth["ip"]).network
        return ipaddress.ip_address(remote_addr) in net
    except ValueError:
        return False


# ---- Wi-Fi scan -----------------------------------------------------------
def wifi_scan(iface):
    """Available networks on `iface`: [{ssid, signal, security}], strongest first."""
    _nmcli("device", "wifi", "rescan", "ifname", iface, timeout=15)
    r = _nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
               "ifname", iface, timeout=15)
    seen, out = set(), []
    for line in r.stdout.splitlines():
        # nmcli -t escapes ':' inside fields as '\:'; SSID is field 0
        parts = line.split(":")
        if not parts or not parts[0]:
            continue
        ssid = parts[0].replace("\\:", ":")
        if ssid in seen:
            continue
        seen.add(ssid)
        try:
            signal = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        except ValueError:
            signal = 0
        security = parts[2] if len(parts) > 2 else ""
        out.append({"ssid": ssid, "signal": signal, "security": security})
    out.sort(key=lambda x: x["signal"], reverse=True)
    return out


# ---- apply configuration --------------------------------------------------
def _connection_for(iface):
    return netinfo.interface(iface).get("connection")


def set_ipv4(iface, method, ip=None, gateway=None, dns=None):
    """Set DHCP ('auto') or Static ('manual', with ip[/prefix], gateway, dns)."""
    con = _connection_for(iface)
    if not con:
        return False, f"{iface}: no active connection to modify"
    if method == "auto":
        r = _nmcli("connection", "modify", con,
                   "ipv4.method", "auto",
                   "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", "")
    elif method == "manual":
        if not ip:
            return False, "static requires an IP address (e.g. 192.168.1.50/24)"
        if "/" not in ip:
            ip = ip + "/24"
        args = ["connection", "modify", con,
                "ipv4.method", "manual", "ipv4.addresses", ip]
        args += ["ipv4.gateway", gateway or ""]
        args += ["ipv4.dns", dns or ""]
        r = _nmcli(*args)
    else:
        return False, f"unknown method: {method}"
    if r.returncode != 0:
        return False, (r.stderr or "nmcli modify failed").strip()
    up = _nmcli("connection", "up", con, timeout=30)
    if up.returncode != 0:
        return False, (up.stderr or "nmcli up failed").strip()
    return True, con


def wifi_connect(iface, ssid, password=None):
    """Connect `iface` to `ssid` (optionally with password)."""
    if not ssid:
        return False, "ssid required"
    args = ["device", "wifi", "connect", ssid, "ifname", iface]
    if password:
        args += ["password", password]
    r = _nmcli(*args, timeout=40)
    if r.returncode != 0:
        return False, (r.stderr or "connect failed").strip()
    return True, ssid
