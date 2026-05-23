"""
Padel Replay - Stream Relay (local)
Re-encodes the RTSP camera stream and pushes it to the EC2 cloud recorder via RTMP.
Uses libx264 ultrafast to generate clean timestamps (camera H.264 has non-monotonic DTS).
"""
import os
import shutil
import subprocess
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

RTSP_URL        = os.getenv("RTSP_URL")
EC2_STREAM_URL  = os.getenv("EC2_STREAM_URL") or os.getenv("EC2_RTMP_URL") or os.getenv("EC2_RTSP_URL")

if not RTSP_URL:
    print("[relay] ERROR: RTSP_URL not set in .env", flush=True)
    exit(1)
if not EC2_STREAM_URL:
    print("[relay] ERROR: EC2_STREAM_URL not set in .env", flush=True)
    exit(1)

ffmpeg = shutil.which("ffmpeg")
if not ffmpeg:
    for p in [r"C:\Program Files\ffmpeg\bin\ffmpeg.exe", r"C:\ffmpeg\bin\ffmpeg.exe"]:
        if os.path.isfile(p):
            ffmpeg = p
            break
if not ffmpeg:
    print("[relay] ERROR: ffmpeg not found in PATH", flush=True)
    exit(1)

cmd = [
    ffmpeg,
    "-rtsp_transport", "tcp",
    "-fflags", "+genpts+nobuffer",
    "-i", RTSP_URL,
    "-map", "0:v:0",
    "-c:v", "libx264",
    "-preset", "ultrafast",
    "-tune", "zerolatency",
    "-x264-params", "bframes=0",   # no B-frames → no reorder delay
    "-crf", "23",
    "-r", "30",
    "-g", "30",                    # keyframe every 1s → segment boundaries within 1s of target
    "-f", "flv",
    EC2_STREAM_URL,
]

print(f"\n{'='*50}", flush=True)
print(f"  PADEL REPLAY - STREAM RELAY", flush=True)
print(f"{'='*50}", flush=True)
print(f"  RTSP: {RTSP_URL}", flush=True)
print(f"  EC2:  {EC2_STREAM_URL}", flush=True)
print(f"{'='*50}\n", flush=True)

while True:
    print("[relay] Connecting to camera...", flush=True)
    subprocess.run(cmd)
    print("[relay] Disconnected. Retrying in 5s...", flush=True)
    time.sleep(5)
