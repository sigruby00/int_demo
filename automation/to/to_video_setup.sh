#!/bin/bash
# usage: to_video_setup.sh nict|plain   (runs on the TO)
V=${1:-nict}
case "$V" in plain) SCRIPT=/home/bitmeister/tus/int_demo/q_to_video_receiver.sh;; *) SCRIPT=/home/bitmeister/tus/int_demo/q_to_video_receiver_nict.sh;; esac
export DISPLAY=:0
[ -f /run/user/1000/gdm/Xauthority ] && export XAUTHORITY=/run/user/1000/gdm/Xauthority
pkill -f "gst-launch-1.0" 2>/dev/null; pkill -f "q_to_video_receiver" 2>/dev/null
tmux kill-session -t to_video 2>/dev/null; sleep 1
tmux new-session -d -s to_video \
  "export DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY; while true; do bash $SCRIPT 2>&1 | tee -a /tmp/q_to_video.log; echo '[WARN] video receiver ended, restart in 3s'; sleep 3; done"
(crontab -l 2>/dev/null | grep -v to_video; echo "@reboot sleep 40 && /home/bitmeister/to_recv/to_video_setup.sh $V") | crontab -
sleep 5
echo "$(hostname): script=$(basename $SCRIPT) gst=$(pgrep -fc gst-launch-1.0) tmux=[$(tmux ls | cut -d: -f1 | tr '\n' ' ')] cron=$(crontab -l | grep -c to_video)"
tail -2 /tmp/q_to_video.log | cut -c1-110
