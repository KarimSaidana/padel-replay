"""
Padel Replay - Cloud Recorder (segment-based)
FFmpeg records the RTSP stream as rolling 5-second MPEG-TS segments on disk.
No Python frame buffer. No JPEG. No re-encode. Original camera quality.
On /save: concatenate the last 30s of segments into MP4 and upload to S3.
"""
import glob
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone

import boto3
from flask import Flask, jsonify, request

RTMP_URL       = os.environ.get("RTMP_URL", "rtsp://localhost:8554/live/stream")
S3_BUCKET      = os.environ["S3_BUCKET"]
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
AUTH_TOKEN     = os.environ["RECORDER_AUTH_TOKEN"]
REGION         = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")

SEGMENT_DIR     = "/tmp/padel_segments"
SEGMENT_SECONDS = 5
KEEP_SEGMENTS   = 8    # 8 × 5s = 40s rolling buffer
CLIP_SECONDS    = 30

s3     = boto3.client("s3", region_name=REGION)
dynamo = boto3.resource("dynamodb", region_name=REGION).Table(DYNAMODB_TABLE)

_proc      = None
_proc_lock = threading.Lock()

app = Flask(__name__)


def recording_loop():
    global _proc
    os.makedirs(SEGMENT_DIR, exist_ok=True)

    while True:
        cmd = [
            "ffmpeg", "-y",
            "-i", RTMP_URL,
            "-c:v", "copy",             # stream copy — zero decode/encode, zero quality loss
            "-an",
            "-f", "segment",
            "-segment_time", str(SEGMENT_SECONDS),
            "-segment_wrap", str(KEEP_SEGMENTS),
            "-segment_format", "mpegts", # TS is always readable mid-write, unlike MP4
            "-reset_timestamps", "1",
            os.path.join(SEGMENT_DIR, "seg_%03d.ts"),
        ]
        print("[recorder] Starting FFmpeg segment recording...", flush=True)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with _proc_lock:
            _proc = proc
        proc.wait()
        with _proc_lock:
            _proc = None
        print("[recorder] FFmpeg stopped. Restarting in 5s...", flush=True)
        time.sleep(5)


def is_recording():
    with _proc_lock:
        return _proc is not None and _proc.poll() is None


def complete_segments():
    """Segments sorted oldest→newest, excluding the one FFmpeg is currently writing."""
    segs = sorted(glob.glob(os.path.join(SEGMENT_DIR, "seg_*.ts")), key=os.path.getmtime)
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
        return jsonify({"error": "No segments yet — is stream_relay.py running?"}), 503

    # Ceiling division: how many 5s segments do we need for 30s?
    n_needed  = -(-CLIP_SECONDS // SEGMENT_SECONDS)   # e.g. 6
    clip_segs = segs[-n_needed:] if len(segs) >= n_needed else segs
    print(f"[save] Using {len(clip_segs)} segments (~{len(clip_segs) * SEGMENT_SECONDS}s)", flush=True)

    uid         = uuid.uuid4().hex[:8]
    concat_path = f"/tmp/concat_{uid}.txt"
    output_path = f"/tmp/replay_{uid}.mp4"

    try:
        with open(concat_path, "w") as f:
            for seg in clip_segs:
                f.write(f"file '{seg}'\n")

        result = subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_path,
            "-c:v", "copy",
            "-movflags", "+faststart",
            output_path,
        ], capture_output=True)

        if result.returncode != 0:
            print(f"[save] FFmpeg concat failed: {result.stderr.decode()[-300:]}", flush=True)
            return jsonify({"error": "Encoding failed"}), 500

        filename = f"replay_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uid}.mp4"
        print(f"[save] Uploading {filename} to S3...", flush=True)
        s3.upload_file(output_path, S3_BUCKET, f"replays/{filename}",
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
                os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    threading.Thread(target=recording_loop, daemon=True).start()
    print(f"[recorder] Segment={SEGMENT_SECONDS}s  Buffer={KEEP_SEGMENTS * SEGMENT_SECONDS}s  Clip={CLIP_SECONDS}s", flush=True)
    app.run(host="0.0.0.0", port=5000, threaded=True)
