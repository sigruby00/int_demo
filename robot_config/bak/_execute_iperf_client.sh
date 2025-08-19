#!/bin/bash
cd /home/ubuntu/shared/int_demo  # config.py 있는 디렉토리로 이동
# Python을 사용해 config.py의 to_id 값을 읽기
TO_ID=$(python3 -c "import config; print(config.to_id)")

# IP 주소 구성
TARGET_IP="10.100.30.2${TO_ID}"

# iperf3 실행
iperf3 -c "$TARGET_IP" -u -b 35M -t 86400