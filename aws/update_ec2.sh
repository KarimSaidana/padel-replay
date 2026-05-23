#!/bin/bash
set -e

# Download latest cloud_recorder.py
aws s3 cp s3://padel-deploy-578386816947-eu-central-1/cloud_recorder.py /opt/padel-recorder/cloud_recorder.py --region eu-central-1

# Update RTMP_URL to RTSP in env file
python3 - << 'PYEOF'
path = "/opt/padel-recorder/env"
content = open(path).read()
content = content.replace("rtmp://localhost:1935/live/stream", "rtsp://localhost:8554/live/stream")
if "RTMP_URL" not in content:
    content = content.rstrip() + "\nRTMP_URL=rtsp://localhost:8554/live/stream\n"
open(path, "w").write(content)
print("env updated:", open(path).read())
PYEOF

# Restart service
systemctl restart padel-recorder
sleep 3
systemctl is-active padel-recorder && echo "SERVICE RUNNING" || echo "SERVICE FAILED"
