#!/bin/bash

# 수신할 포트 설정
PORT=5000
WIDTH=640
HEIGHT=480

echo "🎥 Waiting for incoming H.264 stream on UDP port ${PORT} with size ${WIDTH}x${HEIGHT}..."

gst-launch-1.0 -v \
  udpsrc port=$PORT caps="application/x-rtp, media=video, encoding-name=H264, payload=96" ! \
  rtph264depay ! \
  avdec_h264 ! \
  videoconvert ! \
  videoscale ! video/x-raw,width=$WIDTH,height=$HEIGHT ! \
  ximagesink force-aspect-ratio=false sync=false
