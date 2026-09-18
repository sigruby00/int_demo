#!/usr/bin/env python3
"""Patch peripherals/launch/include/ldlidar_LD19.launch.py (inside the
MentorPi container) so the LD19 driver publishes /scan_ld19 and
webnav/ros/scan_filter.py republishes the near-range-filtered scan as
/scan_raw. Idempotent; keeps a .orig backup. `--revert` restores it.

  docker exec -u ubuntu MentorPi python3 /home/ubuntu/shared/int_demo/automation/patch_ld19_launch.py [--revert] [--min-range 0.30]
"""
import argparse
import os
import re
import shutil
import sys

# lidar.launch.py resolves the include via get_package_share_directory(), i.e.
# the install/ copy (need_compile is False on the robots), so patch every copy.
LAUNCHES = [os.path.expanduser(p) for p in (
    "~/ros2_ws/src/peripherals/launch/include/ldlidar_LD19.launch.py",
    "~/ros2_ws/install/peripherals/share/peripherals/launch/include/ldlidar_LD19.launch.py",
) if os.path.exists(os.path.expanduser(p))]
FILTER = "/home/ubuntu/shared/int_demo/webnav/ros/scan_filter.py"
MARK = "# --- int_demo scan_filter ---"

ap = argparse.ArgumentParser()
ap.add_argument("--revert", action="store_true")
ap.add_argument("--min-range", default="0.30")
a = ap.parse_args()

def patch(LAUNCH):
    orig = LAUNCH + ".orig"
    if a.revert:
        if os.path.exists(orig):
            shutil.copy2(orig, LAUNCH)
            print("reverted from", orig)
        else:
            print("no .orig backup, nothing to revert")
        return

    src = open(LAUNCH).read()
    if MARK in src:
        print("already patched:", LAUNCH)
        return
    if "remappings=[('scan', scan_raw)]" not in src:
        print("unexpected launch file content, not patching", file=sys.stderr)
        return 1
    if not os.path.exists(orig):
        shutil.copy2(LAUNCH, orig)

    src = src.replace("remappings=[('scan', scan_raw)]",
                      "remappings=[('scan', 'scan_ld19')]")
    if "ExecuteProcess" not in src:
        src = src.replace("from launch_ros.actions import Node",
                          "from launch_ros.actions import Node\n"
                          "from launch.actions import ExecuteProcess", 1)
    node_block = f"""
    {MARK}
    # LD19 -> /scan_ld19 -> scan_filter (drop self/cable returns) -> scan_raw
    scan_filter = ExecuteProcess(
        cmd=['python3', '{FILTER}',
             '--in', '/scan_ld19', '--out', scan_raw, '--min-range', '{a.min_range}'],
        output='screen',
    )
"""
    src = re.sub(r"\n(\s*)return LaunchDescription\(\[",
                 node_block + r"\n\1return LaunchDescription([", src, count=1)
    src = src.replace("        ld19_node,\n    ])", "        ld19_node,\n        scan_filter,\n    ])", 1)
    open(LAUNCH, "w").write(src)
    print("patched:", LAUNCH, "(backup:", orig + ")")


rc = 0
for _l in LAUNCHES:
    rc |= patch(_l) or 0
if not LAUNCHES:
    print('ldlidar_LD19.launch.py not found', file=sys.stderr); rc = 1
sys.exit(rc)
