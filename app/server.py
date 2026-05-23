"""
Padel Replay — Flask server (Python replacement for server.js)
"""

import json
import os
import socket
import threading
import time
from datetime import datetime
from pathlib import Path

import logging

import boto3
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template_string, send_from_directory
import paho.mqtt.client as mqtt

logging.getLogger("werkzeug").setLevel(logging.ERROR)

import recorder

# ── Load .env from project root ───────────────────────────────────────
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Config ────────────────────────────────────────────────────────────
CAMERA_URL   = os.getenv("RTSP_URL", "rtsp://karimsa:kikoukikou@172.20.10.12:554/stream1")
BUTTON_TOPIC = os.getenv("BUTTON_TOPIC", "zigbee2mqtt/padel_button")
CLIP_SECONDS = 30
PORT         = 3000

_mqtt_url  = os.getenv("MQTT_URL", "mqtt://localhost:1883").replace("mqtt://", "")
_parts     = _mqtt_url.split(":")
MQTT_HOST  = _parts[0]
MQTT_PORT  = int(_parts[1]) if len(_parts) > 1 else 1883

S3_BUCKET           = os.getenv("S3_BUCKET")
S3_REGION           = os.getenv("S3_REGION", "us-east-1")
S3_ACCESS_KEY_ID    = os.getenv("S3_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY")

BASE_DIR       = Path(__file__).parent.parent
CLIPS_DIR      = BASE_DIR / "clips"
WATERMARK_PATH = BASE_DIR / "watermark.png"

CLIPS_DIR.mkdir(exist_ok=True)
APP_STARTED_AT = datetime.utcnow().isoformat()

# ── S3 ────────────────────────────────────────────────────────────────
_s3 = None
if all([S3_BUCKET, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY]):
    _s3 = boto3.client(
        "s3",
        region_name=S3_REGION,
        aws_access_key_id=S3_ACCESS_KEY_ID,
        aws_secret_access_key=S3_SECRET_ACCESS_KEY,
    )


def upload_to_s3(filepath, filename):
    key = f"replays/{filename}"
    _s3.upload_file(
        filepath, S3_BUCKET, key,
        ExtraArgs={"ACL": "public-read", "ContentType": "video/mp4"},
    )
    return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{key}"


# ── Replay logic ──────────────────────────────────────────────────────
_creating_clip = False
_last_trigger  = 0.0


def trigger_replay(action="button"):
    global _creating_clip, _last_trigger

    now = time.time()
    if now - _last_trigger < 2.0:
        return
    _last_trigger = now

    if _creating_clip:
        return

    if not recorder.is_connected():
        print("[replay] Camera not connected — clip skipped.", flush=True)
        return

    _creating_clip = True
    threading.Thread(target=_do_replay, args=(action,), daemon=True).start()


def _do_replay(action):
    global _creating_clip
    try:
        print(f"[replay] Button triggered ({action})", flush=True)
        result = recorder.save_replay(
            str(CLIPS_DIR),
            watermark_path=str(WATERMARK_PATH) if WATERMARK_PATH.is_file() else None,
        )
        if not result:
            return

        if _s3:
            try:
                url = upload_to_s3(result["path"], result["filename"])
                print(f"[replay] Saved to S3: {url}", flush=True)
            except Exception as e:
                print(f"[replay] S3 upload failed: {e}", flush=True)
    finally:
        _creating_clip = False


# ── MQTT ──────────────────────────────────────────────────────────────
def connect_mqtt():
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print(f"[mqtt] Connected ({MQTT_HOST}:{MQTT_PORT})", flush=True)
            client.subscribe(BUTTON_TOPIC)
        else:
            print(f"[mqtt] Connection failed rc={rc}", flush=True)

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            action = str(payload.get("action", ""))
            if any(x in action for x in ["single", "double", "long", "hold"]):
                trigger_replay(action)
        except Exception:
            pass

    # Handle both paho-mqtt < 2.0 and >= 2.0
    try:
        from paho.mqtt.enums import CallbackAPIVersion
        client = mqtt.Client(CallbackAPIVersion.VERSION1)
    except (ImportError, AttributeError):
        client = mqtt.Client()

    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect_async(MQTT_HOST, MQTT_PORT)
        client.loop_start()
    except Exception as e:
        print(f"[mqtt] Could not connect: {e}", flush=True)
    return client


# ── Flask ─────────────────────────────────────────────────────────────
app = Flask(__name__)


def get_clips():
    clips = []
    for f in CLIPS_DIR.iterdir():
        if f.suffix == ".mp4" and not f.name.startswith("_") and f.stat().st_size > 1000:
            clips.append({
                "file":  f.name,
                "mtime": f.stat().st_mtime,
                "size":  f.stat().st_size,
            })
    return sorted(clips, key=lambda c: c["mtime"], reverse=True)


def fmt_mb(b):
    return f"{b / 1024 / 1024:.1f} MB"


def fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%b %d · %H:%M:%S")


HTML = r"""<!doctype html>
<html>
<head>
  <title>Padel Replay Feed</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="5" />
  <style>
    :root {
      --bg: #07130f; --panel: #0f2019; --border: rgba(255,255,255,0.1);
      --text: #f4fff9; --muted: #a8beb3; --accent: #b6ff3b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: Inter, Arial, sans-serif;
      background: radial-gradient(circle at top left, rgba(182,255,59,0.14), transparent 30%),
                  linear-gradient(180deg, #07130f 0%, #091712 100%);
      color: var(--text); min-height: 100vh;
    }
    .topbar {
      position: sticky; top: 0; z-index: 20;
      background: rgba(7,19,15,0.94); backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--border);
    }
    .topbar-inner {
      max-width: 1180px; margin: 0 auto; padding: 18px;
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .logo {
      width: 44px; height: 44px; border-radius: 14px;
      background: linear-gradient(135deg, var(--accent), #fff); color: #07130f;
      display: flex; align-items: center; justify-content: center;
      font-weight: 900; letter-spacing: -1px; box-shadow: 0 12px 35px rgba(182,255,59,0.18);
    }
    .brand h1 { margin: 0; font-size: 20px; line-height: 1.1; }
    .brand span { display: block; margin-top: 3px; color: var(--muted); font-size: 13px; }
    .status { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 18px var(--accent); }
    .dot.red { background: #ff4444; box-shadow: 0 0 18px #ff4444; }
    .hero { max-width: 1180px; margin: 0 auto; padding: 34px 18px 20px; }
    .hero-card {
      background: linear-gradient(135deg, rgba(182,255,59,0.12), transparent 34%), var(--panel);
      border: 1px solid var(--border); border-radius: 28px; padding: 28px;
      display: grid; grid-template-columns: 1.4fr 0.6fr; gap: 24px;
      box-shadow: 0 24px 80px rgba(0,0,0,0.28);
    }
    .hero h2 { margin: 0; font-size: clamp(30px,5vw,56px); line-height: 0.98; letter-spacing: -2px; }
    .hero p { margin: 16px 0 0; color: var(--muted); font-size: 16px; line-height: 1.55; }
    .hero-side {
      background: rgba(255,255,255,0.04); border: 1px solid var(--border);
      border-radius: 22px; padding: 18px; display: flex; flex-direction: column; gap: 16px;
    }
    .stat { display: flex; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
    .stat:last-child { border-bottom: 0; padding-bottom: 0; }
    .stat-label { color: var(--muted); font-size: 13px; }
    .stat-value { font-weight: 800; font-size: 18px; }
    .controls {
      max-width: 1180px; margin: 0 auto; padding: 0 18px 20px;
      display: flex; gap: 12px; flex-wrap: wrap; align-items: center; justify-content: space-between;
    }
    .hint { color: var(--muted); font-size: 14px; }
    .btn {
      display: inline-flex; align-items: center; justify-content: center;
      border-radius: 999px; padding: 12px 16px; font-weight: 800;
      text-decoration: none; border: 1px solid transparent; font-size: 14px;
    }
    .primary { background: var(--accent); color: #07130f; }
    .secondary { background: rgba(255,255,255,0.06); color: var(--text); border-color: var(--border); }
    button.btn { cursor: pointer; font-family: inherit; }
    .grid {
      max-width: 1180px; margin: 0 auto; padding: 0 18px 50px;
      display: grid; grid-template-columns: repeat(auto-fill, minmax(310px,1fr)); gap: 18px;
    }
    .clip-card {
      background: rgba(15,32,25,0.9); border: 1px solid var(--border);
      border-radius: 24px; overflow: hidden; box-shadow: 0 20px 55px rgba(0,0,0,0.22);
    }
    .video-wrap { position: relative; background: #000; }
    video { width: 100%; display: block; aspect-ratio: 16/9; object-fit: cover; background: #000; }
    .badge {
      position: absolute; top: 12px; left: 12px;
      background: rgba(0,0,0,0.72); border: 1px solid rgba(255,255,255,0.16);
      color: white; padding: 7px 10px; border-radius: 999px; font-size: 12px; font-weight: 800;
    }
    .clip-body { padding: 16px; display: flex; flex-direction: column; gap: 16px; }
    .clip-body h3 { margin: 0; font-size: 18px; }
    .clip-body p { margin: 5px 0 0; color: var(--muted); font-size: 13px; }
    .clip-actions { display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; }
    .clip-actions .btn { width: 100%; }
    .empty { max-width: 1180px; margin: 0 auto; padding: 30px 18px 70px; }
    .empty-card {
      border: 1px dashed rgba(255,255,255,0.18); border-radius: 24px; padding: 34px;
      background: rgba(255,255,255,0.035); text-align: center;
    }
    .empty-card h3 { margin: 0; font-size: 24px; }
    .empty-card p { color: var(--muted); margin: 10px auto 0; max-width: 520px; line-height: 1.5; }
    @media (max-width: 760px) {
      .topbar-inner { flex-direction: column; align-items: flex-start; }
      .hero-card { grid-template-columns: 1fr; padding: 22px; }
      .controls { flex-direction: column; align-items: stretch; }
      .controls .btn { width: 100%; }
      .grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 430px) { .clip-actions { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">
        <div class="logo">PR</div>
        <div>
          <h1>Padel Replay</h1>
          <span>Instant highlights for your court</span>
        </div>
      </div>
      <div class="status">
        <span class="dot {% if not status.connected %}red{% endif %}"></span>
        {% if status.connected %}
          Camera live &middot; {{ status.resolution }} @ {{ status.fps }}fps &middot; Auto-refresh 5s
        {% else %}
          Camera not connected &middot; Auto-refresh 5s
        {% endif %}
      </div>
    </div>
  </header>

  <main>
    <section class="hero">
      <div class="hero-card">
        <div>
          <h2>Save the point. Share the moment.</h2>
          <p>Press the court button after a great rally and the system automatically saves the last {{ clip_seconds }} seconds of video.</p>
        </div>
        <div class="hero-side">
          <div class="stat">
            <div class="stat-label">Replay window</div>
            <div class="stat-value">{{ clip_seconds }}s</div>
          </div>
          <div class="stat">
            <div class="stat-label">Buffer loaded</div>
            <div class="stat-value">{{ status.buffer_seconds }}s</div>
          </div>
          <div class="stat">
            <div class="stat-label">Available clips</div>
            <div class="stat-value">{{ clips|length }}</div>
          </div>
        </div>
      </div>
    </section>

    <section class="controls">
      <div class="hint">Use the physical court button or the manual trigger below for testing.</div>
      <a class="btn primary" href="/trigger">Create test replay</a>
    </section>

    {% if clips %}
    <section class="grid">
      {% for clip in clips %}
      <article class="clip-card">
        <div class="video-wrap">
          <video controls playsinline preload="metadata" src="/clips/{{ clip.file }}"></video>
          <div class="badge">Replay #{{ clips|length - loop.index0 }}</div>
        </div>
        <div class="clip-body">
          <div>
            <h3>Point replay</h3>
            <p>{{ fmt_time(clip.mtime) }} &middot; {{ fmt_mb(clip.size) }}</p>
          </div>
          <div class="clip-actions">
            <a class="btn secondary" href="/clips/{{ clip.file }}" target="_blank">Open</a>
            <a class="btn secondary" href="/clips/{{ clip.file }}" download>Download</a>
            <button class="btn primary" onclick="shareClip('{{ clip.file }}')">Share</button>
          </div>
        </div>
      </article>
      {% endfor %}
    </section>
    {% else %}
    <section class="empty">
      <div class="empty-card">
        <h3>No replays yet</h3>
        <p>Wait around {{ clip_seconds }} seconds after starting the system, then press the court button. Your first replay will appear here automatically.</p>
      </div>
    </section>
    {% endif %}
  </main>

  <script>
    async function shareClip(fileName) {
      const url = window.location.origin + '/clips/' + fileName;
      try {
        if (navigator.share) { await navigator.share({ title: 'Padel Replay', url }); return; }
        await navigator.clipboard.writeText(url);
        alert('Link copied — paste it on WhatsApp, Instagram, or anywhere.');
      } catch {
        try { await navigator.clipboard.writeText(url); alert('Link copied.'); }
        catch { alert('Sharing not available. Use Open or Download instead.'); }
      }
    }
  </script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(
        HTML,
        clips=get_clips(),
        status=recorder.get_status(),
        clip_seconds=CLIP_SECONDS,
        fmt_mb=fmt_mb,
        fmt_time=fmt_time,
    )


@app.route("/clips/<path:filename>")
def serve_clip(filename):
    if not (CLIPS_DIR / filename).is_file():
        abort(404)
    return send_from_directory(str(CLIPS_DIR), filename, mimetype="video/mp4")


@app.route("/trigger")
def trigger():
    trigger_replay("manual")
    return redirect("/")


@app.route("/health")
def health():
    return jsonify({
        "status":        "ok",
        "recorder":      recorder.get_status(),
        "clips":         len(get_clips()),
        "app_started_at": APP_STARTED_AT,
    })


# ── Startup ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    recorder.start(CAMERA_URL)
    connect_mqtt()

    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "localhost"

    print(f"\n[server] Replay feed:  http://localhost:{PORT}", flush=True)
    print(f"[server] On your phone: http://{local_ip}:{PORT}", flush=True)
    print(f"[server] Health check:  http://localhost:{PORT}/health\n", flush=True)

    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
