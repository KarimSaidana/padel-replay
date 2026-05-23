"""
Padel Replay Lambda Handler
Handles clip creation, web UI serving, and metadata queries
"""

import json
import os
import subprocess
import boto3
import uuid
from datetime import datetime, timedelta

# AWS Clients (control-plane only — media clients need per-stream endpoints)
kinesis_client = boto3.client("kinesisvideo")
s3_client = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

# Environment
KVS_STREAM_NAME = os.environ["KVS_STREAM_NAME"]
S3_BUCKET = os.environ["S3_BUCKET"]
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
LAMBDA_AUTH_TOKEN = os.environ["LAMBDA_AUTH_TOKEN"]
# Lambda sets AWS_REGION automatically; APP_REGION is our CloudFormation alias
REGION = os.environ.get("APP_REGION") or os.environ.get("AWS_REGION", "us-east-1")

FFMPEG = "/opt/bin/ffmpeg"
WATERMARK = "/var/task/watermark.png"  # bundled in Lambda zip

table = dynamodb.Table(DYNAMODB_TABLE)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def auth_check(headers):
    token = headers.get("authorization", "").replace("Bearer ", "")
    return token == LAMBDA_AUTH_TOKEN


def http_response(status_code, body):
    if isinstance(body, dict):
        body = json.dumps(body)
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": body,
    }


def html_response(html):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html; charset=utf-8", "Access-Control-Allow-Origin": "*"},
        "body": html,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KVS — GET LAST 30 SECONDS AS MP4
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_clip_from_kvs():
    """
    Uses GetClip (archived-media API) to pull the last 30 seconds from the
    Kinesis Video Stream. Returns raw MP4 bytes or None on failure.

    GetClip is the correct API for this: it returns a self-contained MP4 that
    covers a requested time range. GetMedia (with StartSelectorType: NOW)
    would only deliver future frames and is not suitable here.
    """
    try:
        # Each stream has its own endpoint — fetch it first
        ep_response = kinesis_client.get_data_endpoint(
            StreamName=KVS_STREAM_NAME,
            APIName="GET_CLIP",
        )
        endpoint = ep_response["DataEndpoint"]

        archived = boto3.client("kinesis-video-archived-media", endpoint_url=endpoint)

        now = datetime.utcnow()
        start = now - timedelta(seconds=35)  # 5 s extra so we always have 30 s

        response = archived.get_clip(
            StreamName=KVS_STREAM_NAME,
            ClipFragmentSelector={
                "FragmentSelectorType": "SERVER_TIMESTAMP",
                "TimestampRange": {
                    "StartTimestamp": start,
                    "EndTimestamp": now,
                },
            },
        )

        video_bytes = response["Payload"].read()
        print(f"[kvs] Retrieved {len(video_bytes):,} bytes from KVS")
        return video_bytes if video_bytes else None

    except Exception as e:
        print(f"[kvs] Error retrieving clip: {e}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENCODE + UPLOAD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def encode_and_upload(video_bytes, filename):
    """
    GetClip already returns an MP4 container.  We still pipe it through FFmpeg
    to add -movflags +faststart (web streaming) and optionally burn the
    watermark.  Returns {"filename", "s3_url"} or None on failure.
    """
    uid = uuid.uuid4().hex[:8]
    input_path = f"/tmp/{uid}_in.mp4"
    output_path = f"/tmp/{filename}"

    try:
        with open(input_path, "wb") as f:
            f.write(video_bytes)

        has_watermark = os.path.isfile(WATERMARK)

        if has_watermark:
            cmd = [
                FFMPEG, "-y",
                "-i", input_path,
                "-i", WATERMARK,
                "-filter_complex", "[1:v]scale=200:200[wm];[0:v][wm]overlay=0:0[out]",
                "-map", "[out]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-movflags", "+faststart",
                "-an",
                output_path,
            ]
        else:
            cmd = [
                FFMPEG, "-y",
                "-i", input_path,
                "-c:v", "copy",
                "-movflags", "+faststart",
                "-an",
                output_path,
            ]

        result = subprocess.run(cmd, capture_output=True, timeout=90)
        if result.returncode != 0:
            print(f"[ffmpeg] Error: {result.stderr.decode()[-500:]}")
            return None

        with open(output_path, "rb") as f:
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=f"replays/{filename}",
                Body=f.read(),
                ContentType="video/mp4",
                ACL="public-read",
            )

        s3_url = f"https://{S3_BUCKET}.s3.{REGION}.amazonaws.com/replays/{filename}"
        print(f"[s3] Uploaded: {s3_url}")
        return {"filename": filename, "s3_url": s3_url}

    except Exception as e:
        print(f"[encode] Error: {e}")
        return None

    finally:
        for p in (input_path, output_path):
            try:
                os.remove(p)
            except OSError:
                pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DYNAMODB
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def save_clip_metadata(clip_id, filename, s3_url, action="button"):
    try:
        ts = int(datetime.utcnow().timestamp() * 1000)
        table.put_item(
            Item={
                "clip_id": clip_id,
                "timestamp": ts,
                "filename": filename,
                "s3_url": s3_url,
                "action": action,
                "created_at": datetime.utcnow().isoformat(),
                "ttl": int(datetime.utcnow().timestamp()) + 90 * 24 * 3600,
            }
        )
        return True
    except Exception as e:
        print(f"[dynamo] Error saving metadata: {e}")
        return False


def get_clips_from_db(limit=20):
    try:
        response = table.scan(Limit=limit)
        clips = response.get("Items", [])
        clips.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return clips
    except Exception as e:
        print(f"[dynamo] Error querying clips: {e}")
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ROUTES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def route_health():
    clips_count = len(get_clips_from_db(limit=1000))
    return http_response(200, {
        "status": "ok",
        "stream": KVS_STREAM_NAME,
        "clips": clips_count,
        "timestamp": datetime.utcnow().isoformat(),
    })


def route_trigger(headers, body):
    if not auth_check(headers):
        return http_response(401, {"error": "Unauthorized"})

    print("[trigger] Button press received")

    video_bytes = get_clip_from_kvs()
    if not video_bytes:
        return http_response(503, {"error": "No video data available from KVS"})

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    clip_id = f"replay_{ts}_{uuid.uuid4().hex[:8]}"
    filename = f"{clip_id}.mp4"

    result = encode_and_upload(video_bytes, filename)
    if not result:
        return http_response(500, {"error": "Encoding or upload failed"})

    save_clip_metadata(clip_id, result["filename"], result["s3_url"], action="button")

    print(f"[trigger] Done: {clip_id}")
    return http_response(200, {
        "clip_id": clip_id,
        "s3_url": result["s3_url"],
        "filename": result["filename"],
    })


def route_clips():
    clips = get_clips_from_db(limit=100)
    return http_response(200, {"clips": clips, "count": len(clips)})


def route_index():
    clips = get_clips_from_db(limit=20)

    if clips:
        cards = ""
        for i, clip in enumerate(clips):
            ts = datetime.fromtimestamp(clip["timestamp"] / 1000).strftime("%b %d · %H:%M:%S")
            url = clip["s3_url"]
            name = clip.get("filename", "replay.mp4")
            cards += f"""
            <article class="clip-card">
              <div class="video-wrap">
                <video controls playsinline preload="metadata" src="{url}"></video>
                <div class="badge">Replay #{len(clips) - i}</div>
              </div>
              <div class="clip-body">
                <div>
                  <h3>Point replay</h3>
                  <p>{ts}</p>
                </div>
                <div class="clip-actions">
                  <a class="btn secondary" href="{url}" target="_blank">Open</a>
                  <a class="btn secondary" href="{url}" download="{name}">Download</a>
                  <button class="btn primary" onclick="shareClip('{url}')">Share</button>
                </div>
              </div>
            </article>"""
        grid_section = f'<section class="grid">{cards}</section>'
    else:
        grid_section = """
        <section class="empty">
          <div class="empty-card">
            <h3>No replays yet</h3>
            <p>Press the court button to save a replay. Your first clip will appear here.</p>
          </div>
        </section>"""

    html = f"""<!doctype html>
<html>
<head>
  <title>Padel Replay Feed</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="5" />
  <style>
    :root {{
      --bg: #07130f; --panel: #0f2019; --border: rgba(255,255,255,0.1);
      --text: #f4fff9; --muted: #a8beb3; --accent: #b6ff3b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: Inter, Arial, sans-serif;
      background: radial-gradient(circle at top left, rgba(182,255,59,0.14), transparent 30%),
                  linear-gradient(180deg, #07130f 0%, #091712 100%);
      color: var(--text); min-height: 100vh;
    }}
    .topbar {{
      position: sticky; top: 0; z-index: 20;
      background: rgba(7,19,15,0.94); backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--border);
    }}
    .topbar-inner {{
      max-width: 1180px; margin: 0 auto; padding: 18px;
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
    }}
    .brand {{ display: flex; align-items: center; gap: 12px; }}
    .logo {{
      width: 44px; height: 44px; border-radius: 14px;
      background: linear-gradient(135deg, var(--accent), #fff); color: #07130f;
      display: flex; align-items: center; justify-content: center;
      font-weight: 900; letter-spacing: -1px; box-shadow: 0 12px 35px rgba(182,255,59,0.18);
    }}
    .brand h1 {{ margin: 0; font-size: 20px; line-height: 1.1; }}
    .brand span {{ display: block; margin-top: 3px; color: var(--muted); font-size: 13px; }}
    .status {{ display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }}
    .dot {{ width: 9px; height: 9px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 18px var(--accent); }}
    .hero {{ max-width: 1180px; margin: 0 auto; padding: 34px 18px 20px; }}
    .hero-card {{
      background: linear-gradient(135deg, rgba(182,255,59,0.12), transparent 34%), var(--panel);
      border: 1px solid var(--border); border-radius: 28px; padding: 28px;
      display: grid; grid-template-columns: 1.4fr 0.6fr; gap: 24px;
      box-shadow: 0 24px 80px rgba(0,0,0,0.28);
    }}
    .hero h2 {{ margin: 0; font-size: clamp(30px,5vw,56px); line-height: 0.98; letter-spacing: -2px; }}
    .hero p {{ margin: 16px 0 0; color: var(--muted); font-size: 16px; line-height: 1.55; }}
    .hero-side {{
      background: rgba(255,255,255,0.04); border: 1px solid var(--border);
      border-radius: 22px; padding: 18px; display: flex; flex-direction: column; gap: 16px;
    }}
    .stat {{ display: flex; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }}
    .stat:last-child {{ border-bottom: 0; padding-bottom: 0; }}
    .stat-label {{ color: var(--muted); font-size: 13px; }}
    .stat-value {{ font-weight: 800; font-size: 18px; }}
    .grid {{
      max-width: 1180px; margin: 0 auto; padding: 0 18px 50px;
      display: grid; grid-template-columns: repeat(auto-fill, minmax(310px,1fr)); gap: 18px;
    }}
    .clip-card {{
      background: rgba(15,32,25,0.9); border: 1px solid var(--border);
      border-radius: 24px; overflow: hidden; box-shadow: 0 20px 55px rgba(0,0,0,0.22);
    }}
    .video-wrap {{ position: relative; background: #000; }}
    video {{ width: 100%; display: block; aspect-ratio: 16/9; object-fit: cover; background: #000; }}
    .badge {{
      position: absolute; top: 12px; left: 12px;
      background: rgba(0,0,0,0.72); border: 1px solid rgba(255,255,255,0.16);
      color: white; padding: 7px 10px; border-radius: 999px; font-size: 12px; font-weight: 800;
    }}
    .clip-body {{ padding: 16px; display: flex; flex-direction: column; gap: 16px; }}
    .clip-body h3 {{ margin: 0; font-size: 18px; }}
    .clip-body p {{ margin: 5px 0 0; color: var(--muted); font-size: 13px; }}
    .clip-actions {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; }}
    .clip-actions .btn {{ width: 100%; }}
    .empty {{ max-width: 1180px; margin: 0 auto; padding: 30px 18px 70px; }}
    .empty-card {{
      border: 1px dashed rgba(255,255,255,0.18); border-radius: 24px; padding: 34px;
      background: rgba(255,255,255,0.035); text-align: center;
    }}
    .empty-card h3 {{ margin: 0; font-size: 24px; }}
    .empty-card p {{ color: var(--muted); margin: 10px auto 0; max-width: 520px; line-height: 1.5; }}
    .btn {{
      display: inline-flex; align-items: center; justify-content: center;
      border-radius: 999px; padding: 12px 16px; font-weight: 800;
      text-decoration: none; border: 1px solid transparent; font-size: 14px;
    }}
    .primary {{ background: var(--accent); color: #07130f; cursor: pointer; }}
    .secondary {{ background: rgba(255,255,255,0.06); color: var(--text); border-color: var(--border); }}
    @media (max-width: 760px) {{
      .topbar-inner {{ flex-direction: column; align-items: flex-start; }}
      .hero-card {{ grid-template-columns: 1fr; padding: 22px; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 430px) {{ .clip-actions {{ grid-template-columns: 1fr; }} }}
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
        <span class="dot"></span>
        System live · Auto-refresh 5s
      </div>
    </div>
  </header>
  <main>
    <section class="hero">
      <div class="hero-card">
        <div>
          <h2>Save the point. Share the moment.</h2>
          <p>Press the court button after a great rally and the system automatically saves the last 30 seconds of video.</p>
        </div>
        <div class="hero-side">
          <div class="stat">
            <div class="stat-label">Replay window</div>
            <div class="stat-value">30s</div>
          </div>
          <div class="stat">
            <div class="stat-label">Available clips</div>
            <div class="stat-value">{len(clips)}</div>
          </div>
        </div>
      </div>
    </section>
    {grid_section}
  </main>
  <script>
    async function shareClip(url) {{
      try {{
        if (navigator.share) {{ await navigator.share({{ title: 'Padel Replay', url }}); return; }}
        await navigator.clipboard.writeText(url);
        alert('Link copied — paste on WhatsApp, Instagram, or anywhere.');
      }} catch {{
        try {{ await navigator.clipboard.writeText(url); alert('Link copied.'); }}
        catch {{ alert('Sharing not available.'); }}
      }}
    }}
  </script>
</body>
</html>"""

    return html_response(html)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN HANDLER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    headers = event.get("headers", {})
    body = event.get("body", "")

    print(f"[handler] {method} {path}")

    if path == "/" and method == "GET":
        return route_index()
    elif path == "/health" and method == "GET":
        return route_health()
    elif path == "/trigger" and method == "POST":
        return route_trigger(headers, body)
    elif path == "/clips" and method == "GET":
        return route_clips()
    else:
        return http_response(404, {"error": "Not found"})
