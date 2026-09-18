#!/bin/bash
# Keep the TO-side QoS reporter (throughput/delay/jitter -> NeuroRAT server
# :6789 as robot_pf_data) running in a resilient tmux session. Idempotent.
# Installed by the NeuroRAT server; started at boot via `crontab -l` (@reboot).
cd /home/bitmeister/to_recv || exit 1
tmux has-session -t to_recv 2>/dev/null && exit 0
tmux new-session -d -s to_recv \
  "while true; do python3 -u q_to_udp_receiver_adv_adv.py 2>&1 | tee -a /tmp/q_to_recv.log; echo '[WARN] reporter ended, restart in 3s'; sleep 3; done"
