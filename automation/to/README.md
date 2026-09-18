# TO-side QoS reporter

Runs on each TO mini PC (`mini-to1N`, 10.100.30.2N, user `bitmeister`), NOT on the robots.
Measures enp1s0 receive throughput + UDP delay/jitter (port 6001, fed by the robot's
udpgen) and reports `robot_pf_data` to the NeuroRAT server (10.100.30.241:6789,
socket.io auth `type=to`, `id=to_id`). `to_id` = robot id + 1 (robot4 -> TO .24 -> to_id 5).

Install / re-install on a TO (from the server, key auth as bitmeister):

    ssh toN 'mkdir -p ~/to_recv && cp -f ~/tus/int_demo/config.py ~/to_recv/'   # config.py holds to_id
    scp automation/to/q_to_udp_receiver_adv_adv.py automation/to/run_to_recv.sh toN:~/to_recv/
    ssh toN '~/to_recv/run_to_recv.sh; (crontab -l; echo "@reboot sleep 20 && /home/bitmeister/to_recv/run_to_recv.sh") | crontab -'

`run_to_recv.sh` keeps it in tmux session `to_recv` with auto-restart; log: `/tmp/q_to_recv.log`.
Deployed 2026-09-18 on TO4–TO8. (No passwordless sudo on the TOs -> cron instead of systemd.)

## Video receiver (GStreamer, robot camera -> UDP 5000 -> TO screen)

`to_video_setup.sh nict|plain` (in `~/to_recv/` on each TO) kills stale `gst-launch` instances,
runs `q_to_video_receiver_nict.sh` (or the plain one) in tmux session `to_video` with auto-restart
on DISPLAY=:0, and registers itself `@reboot`. TO7 uses `plain`, the others `nict` (as found running).
Log: `/tmp/q_to_video.log`. Never `pkill -f` these names from an inline ssh command: the pattern
matches the ssh shell itself and kills your session (happened twice).
