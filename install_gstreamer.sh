#!/bin/bash

echo "🔄 Updating package lists..."
sudo apt update

echo "⬇️ Installing GStreamer and all major plugin packages..."
sudo apt install -y \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  gstreamer1.0-doc \
  gstreamer1.0-x \
  gstreamer1.0-gl \
  gstreamer1.0-alsa \
  gstreamer1.0-pulseaudio \
  gstreamer1.0-qt5 \
  gstreamer1.0-gtk3

echo "✅ GStreamer installation complete!"
gst-launch-1.0 --version
