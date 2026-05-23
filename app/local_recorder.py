"""
Padel Replay - Local Recorder
Runs on your Windows PC. FFmpeg reads the camera RTSP directly,
re-encodes with libx264 (needed to fix the camera's non-monotonic timestamps),
and writes rolling 5-second segments to disk.
On /save: concatenate the last 30s → upload to S3.
No relay, no EC2, ~0 RAM, ~25MB disk.
"""
import glob
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv(Path(__file__).parent.parent / ".env")

RTSP_URL       = os.getenv("RTSP_URL")
S3_BUCKET      = os.getenv("S3_BUCKET")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE")
AUTH_TOKEN     = os.getenv("RECORDER_AUTH_TOKEN")
REGION         = os.getenv("AWS_REGION", "eu-central-1")

for var, val in [("RTSP_URL", RTSP_URL), ("S3_BUCKET", S3_BUCKET),
                 ("DYNAMODB_TABLE", DYNAMODB_TABLE), ("RECORDER_AUTH_TOKEN", AUTH_TOKEN)]:
    if not val:
        print(f"[recorder] ERROR: {var} not set in .env")
        exit(1)

SEGMENT_DIR     = Path(__file__).parent.parent / "segments"
SEGMENT_SECONDS = 5
KEEP_SEGMENTS   = 8    # 8 × 5s = 40s rolling buffer
CLIP_SECONDS    = 30

s3     = boto3.client("s3", region_name=REGION)
dynamo = boto3.resource("dynamodb", region_name=REGION).Table(DYNAMODB_TABLE)

_proc      = None
_proc_lock = threading.Lock()

app = Flask(__name__)


def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    for path in [r"C:\Program Files\ffmpeg\bin\ffmpeg.exe", r"C:\ffmpeg\bin\ffmpeg.exe"]:
        if os.path.isfile(path):
            return path
    return "ffmpeg"


def recording_loop():
    global _proc
    SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()

    cmd = [
        ffmpeg, "-y",
        "-rtsp_transport", "tcp",
        "-fflags", "+genpts",           # fix camera non-monotonic timestamps
        "-i", RTSP_URL,
        "-c:v", "libx264",              # re-encode: only way to get clean timestamps from this camera
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-x264-params", "bframes=0",
        "-crf", "23",
        "-r", "30",
        "-g", "30",                     # keyframe every 1s → segment cuts within 1s of target
        "-an",
        "-f", "segment",
        "-segment_time", str(SEGMENT_SECONDS),
        "-segment_wrap", str(KEEP_SEGMENTS),
        "-segment_format", "mpegts",
        "-reset_timestamps", "1",
        str(SEGMENT_DIR / "seg_%03d.ts"),
    ]

    print(f"\n{'='*50}", flush=True)
    print(f"  PADEL REPLAY - LOCAL RECORDER", flush=True)
    print(f"{'='*50}", flush=True)
    print(f"  Camera:  {RTSP_URL}", flush=True)
    print(f"  Buffer:  {KEEP_SEGMENTS * SEGMENT_SECONDS}s  Clip: {CLIP_SECONDS}s", flush=True)
    print(f"{'='*50}\n", flush=True)

    while True:
        print("[recorder] Connecting to camera...", flush=True)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with _proc_lock:
            _proc = proc
        proc.wait()
        with _proc_lock:
            _proc = None
        print("[recorder] Disconnected. Retrying in 5s...", flush=True)
        time.sleep(5)


def is_recording():
    with _proc_lock:
        return _proc is not None and _proc.poll() is None


def complete_segments():
    segs = sorted(SEGMENT_DIR.glob("seg_*.ts"), key=os.path.getmtime)
    return segs[:-1] if len(segs) > 1 else []


@app.route("/health")
def health():
    segs = complete_segments()
    return jsonify({
        "status":         "ok",
        "recording":      is_recording(),
        "segments":       len(segs),
        "buffer_seconds": len(segs) * SEGMENT_SECONDS,
    })


@app.route("/save", methods=["POST"])
def save():
    if request.headers.get("Authorization", "").replace("Bearer ", "") != AUTH_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401

    segs = complete_segments()
    if not segs:
        return jsonify({"error": "No segments yet — is the camera reachable?"}), 503

    n_needed  = -(-CLIP_SECONDS // SEGMENT_SECONDS)
    clip_segs = segs[-n_needed:] if len(segs) >= n_needed else segs
    print(f"[save] Using {len(clip_segs)} segments (~{len(clip_segs) * SEGMENT_SECONDS}s)", flush=True)

    ffmpeg      = find_ffmpeg()
    uid         = uuid.uuid4().hex[:8]
    concat_path = SEGMENT_DIR / f"concat_{uid}.txt"
    output_path = SEGMENT_DIR / f"replay_{uid}.mp4"

    try:
        with open(concat_path, "w") as f:
            for seg in clip_segs:
                f.write(f"file '{Path(seg).as_posix()}'\n")

        result = subprocess.run([
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_path),
            "-c:v", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ], capture_output=True)

        if result.returncode != 0:
            print(f"[save] FFmpeg concat failed: {result.stderr.decode()[-300:]}", flush=True)
            return jsonify({"error": "Encoding failed"}), 500

        filename = f"replay_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uid}.mp4"
        print(f"[save] Uploading {filename} to S3...", flush=True)
        s3.upload_file(str(output_path), S3_BUCKET, f"replays/{filename}",
                       ExtraArgs={"ContentType": "video/mp4"})
        s3_url  = f"https://{S3_BUCKET}.s3.{REGION}.amazonaws.com/replays/{filename}"
        clip_id = f"replay_{uid}"
        ts      = int(time.time() * 1000)

        dynamo.put_item(Item={
            "clip_id":    clip_id,
            "timestamp":  ts,
            "filename":   filename,
            "s3_url":     s3_url,
            "action":     "button",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ttl":        int(time.time()) + 90 * 24 * 3600,
        })

        print(f"[save] Done: {s3_url}", flush=True)
        return jsonify({"clip_id": clip_id, "s3_url": s3_url, "filename": filename})

    except Exception as e:
        print(f"[save] Error: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

    finally:
        for p in [concat_path, output_path]:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    threading.Thread(target=recording_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, threaded=True)
