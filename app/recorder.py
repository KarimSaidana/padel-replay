"""
Camera recorder — background thread that keeps a rolling frame buffer.
Uses OpenCV to decode every frame individually, which gives smooth video
regardless of the camera's RTSP timestamp irregularities.
"""

import os
import cv2
import time
import shutil
import subprocess
import threading
from collections import deque
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────
BUFFER_SECONDS = 30
FALLBACK_FPS   = 15.0
FULL_QUALITY   = True   # False → faster encode, smaller files (for testing)

# ── State ─────────────────────────────────────────────────────────────
_frame_buffer = None
_buffer_lock  = threading.Lock()
_fps          = FALLBACK_FPS
_width        = 0
_height       = 0
_running      = False
_connected    = False
_thread       = None


def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    for path in [
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ]:
        if os.path.isfile(path):
            return path
    return "ffmpeg"


def is_connected():
    return _connected


def is_running():
    return _running


def get_status():
    buf_len  = len(_frame_buffer) if _frame_buffer else 0
    buf_secs = round(buf_len / _fps, 1) if _fps > 0 else 0
    return {
        "running":        _running,
        "connected":      _connected,
        "fps":            round(_fps, 1),
        "resolution":     f"{_width}x{_height}",
        "buffer_seconds": buf_secs,
        "buffer_frames":  buf_len,
    }


def _capture_loop(camera_url):
    global _frame_buffer, _fps, _width, _height, _running, _connected

    _running   = True
    _connected = False

    cap = cv2.VideoCapture(camera_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_url)

    if not cap.isOpened():
        print("[camera] ERROR: Cannot connect to camera.", flush=True)
        _running = False
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    _fps = fps if 1 < fps < 120 else FALLBACK_FPS

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    _width  = w if w > 0 else 1280
    _height = h if h > 0 else 720

    max_frames = int(BUFFER_SECONDS * _fps)
    with _buffer_lock:
        _frame_buffer = deque(maxlen=max_frames)

    _connected = True
    print(f"[camera] Connected: {_width}x{_height} @ {_fps:.1f}fps", flush=True)

    while _running:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        if frame.shape[1] != _width or frame.shape[0] != _height:
            frame = cv2.resize(frame, (_width, _height))
        with _buffer_lock:
            _frame_buffer.append(frame.copy())

    cap.release()
    _connected = False


def start(camera_url):
    global _thread, _running
    if _running:
        return
    _thread = threading.Thread(target=_capture_loop, args=(camera_url,), daemon=True)
    _thread.start()


def stop():
    global _running
    _running = False


def save_replay(clips_dir, watermark_path=None):
    """
    Snapshot the current frame buffer, write a raw MP4, then re-encode to
    browser-ready H.264 via FFmpeg (adding watermark if supplied).
    Returns {"filename", "path", "frames"} or None on failure.
    """
    if not _frame_buffer:
        return None

    with _buffer_lock:
        frames = list(_frame_buffer)

    if not frames:
        return None

    ffmpeg = find_ffmpeg()
    ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path   = os.path.join(clips_dir, f"_raw_{ts}.mp4")
    final_name = f"replay_{ts}.mp4"
    final_path = os.path.join(clips_dir, final_name)

    # Write raw frames with OpenCV
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(raw_path, fourcc, _fps, (_width, _height))
    if not writer.isOpened():
        return None
    for frame in frames:
        writer.write(frame)
    writer.release()

    preset = "slow"    if FULL_QUALITY else "veryfast"
    crf    = "18"      if FULL_QUALITY else "28"

    try:
        if watermark_path and os.path.isfile(watermark_path):
            cmd = [
                ffmpeg, "-y", "-i", raw_path, "-i", watermark_path,
                "-filter_complex",
                "[1:v]scale=200:200[wm];[0:v][wm]overlay=0:0[out]",
                "-map", "[out]",
                "-c:v", "libx264", "-preset", preset, "-crf", crf,
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
                final_path,
            ]
        else:
            cmd = [
                ffmpeg, "-y", "-i", raw_path,
                "-c:v", "libx264", "-preset", preset, "-crf", crf,
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
                final_path,
            ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            return None
    finally:
        try:
            os.remove(raw_path)
        except OSError:
            pass

    print(f"[clip] Saved locally: {final_name}", flush=True)
    return {"filename": final_name, "path": final_path, "frames": len(frames)}
