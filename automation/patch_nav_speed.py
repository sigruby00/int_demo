#!/usr/bin/env python3
"""Scale the nav2 speed limits (velocity_smoother, DWB controller, spin recovery)
by a factor, in src + install copies. Idempotent: the files carry a marker with
the factor already applied; --revert restores the originals from the marker."""
import argparse, re, sys
WS = "/home/ubuntu/ros2_ws"
FILES = {
    "params": [f"{WS}/src/navigation/config/nav2_params.yaml",
               f"{WS}/install/navigation/share/navigation/config/nav2_params.yaml"],
    "dwb": [f"{WS}/src/navigation/config/nav2_controller_dwb.yaml",
            f"{WS}/install/navigation/share/navigation/config/nav2_controller_dwb.yaml"],
}
MARK = "# int_demo nav speed x"
# (regex on the line, which numbers to scale) -- scaled from the ORIGINAL values kept in the marker
RULES = {
    "params": [(r"^(\s*max_velocity:\s*\[)\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*(\].*)$", "vec"),
               (r"^(\s*max_rotational_vel:\s*)([0-9.]+)(.*)$", "one")],
    "dwb": [(r"^(\s*max_vel_x:\s*)([0-9.]+)(.*)$", "one"), (r"^(\s*min_vel_x:\s*)(-?[0-9.]+)(.*)$", "one"),
            (r"^(\s*max_vel_theta:\s*)([0-9.]+)(.*)$", "one"), (r"^(\s*max_speed_xy:\s*)([0-9.]+)(.*)$", "one")],
}

def fmt(v):
    s = f"{v:.3f}".rstrip("0")
    return s + "0" if s.endswith(".") else s      # keep a float literal (ROS param arrays must not mix int/float)

def patch(path, kind, factor):
    try:
        text = open(path).read()
    except FileNotFoundError:
        print(f"[nav_speed] missing: {path}"); return
    m = re.search(re.escape(MARK) + r"([0-9.]+) orig=(\{.*\})\n", text)
    orig = eval(m.group(2)) if m else {}
    if m:                                   # strip the marker; values will be recomputed from orig
        text = text.replace(m.group(0), "")
    out, i = [], 0
    for line in text.splitlines(True):
        new = line
        for rx, mode in RULES[kind]:
            mm = re.match(rx, line)
            if not mm:
                continue
            key = f"{kind}:{i}"
            if mode == "vec":
                base = orig.get(key) or [float(mm.group(2)), float(mm.group(3)), float(mm.group(4))]
                orig[key] = base
                v = [base[0] * factor, base[1], base[2] * factor] if factor else base
                new = f"{mm.group(1)}{fmt(v[0])}, {fmt(v[1])}, {fmt(v[2])}{mm.group(5)}\n"
            else:
                base = orig.get(key) or float(mm.group(2))
                orig[key] = base
                new = f"{mm.group(1)}{fmt(base * factor) if factor else fmt(base)}{mm.group(3)}\n"
            break
        out.append(new); i += 1
    text = "".join(out)
    if factor:
        text = f"{MARK}{factor} orig={orig!r}\n" + text
    open(path, "w").write(text)
    print(f"[nav_speed] {'reverted' if not factor else f'x{factor}'}: {path}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--factor", type=float, default=1.15); ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    for kind, paths in FILES.items():
        for p in paths:
            patch(p, kind, 0 if a.revert else a.factor)
