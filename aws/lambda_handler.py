"""
Padel Replay Lambda Handler
Handles clip creation, web UI serving, and metadata queries
"""

import json
import os
import subprocess
import boto3
import uuid
from datetime import datetime
from urllib.parse import parse_qs
import base64

# AWS Clients
kinesis_client = boto3.client("kinesisvideo")
kvs_media_client = boto3.client("kinesis-video-media")
s3_client = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

# Environment
KVS_STREAM_NAME = os.environ["KVS_STREAM_NAME"]
S3_BUCKET = os.environ["S3_BUCKET"]
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
LAMBDA_AUTH_TOKEN = os.environ["LAMBDA_AUTH_TOKEN"]
REGION = os.environ["AWS_REGION"]

table = dynamodb.Table(DYNAMODB_TABLE)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def auth_check(headers):
    """Verify auth token"""
    token = headers.get("Authorization", "").replace("Bearer ", "")
    if token != LAMBDA_AUTH_TOKEN:
        return False
    return True


def http_response(status_code, body):
    """Format API Gateway response"""
    if isinstance(body, dict):
        body = json.dumps(body)
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": body,
    }


def html_response(html):
    """Format HTML response"""
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html; charset=utf-8", "Access-Control-Allow-Origin": "*"},
        "body": html,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CORE LOGIC
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_kvs_endpoint():
    """Get Kinesis Video Streams media endpoint"""
    try:
        response = kinesis_client.get_data_endpoint(
            StreamName=KVS_STREAM_NAME,
            APIName="GET_MEDIA"
        )
        return response["DataEndpoint"]
    except Exception as e:
        print(f"Error getting KVS endpoint: {e}")
        return None


def get_frame_buffer_from_kvs():
    """
    Query Kinesis Video Streams to get the last 30 seconds of video.
    Returns raw video bytes or None if error.
    """
    try:
        endpoint = get_kvs_endpoint()
        if not endpoint:
            print("Could not get KVS endpoint")
            return None

        # Create media client with specific endpoint
        media_client = boto3.client("kinesis-video-media", endpoint_url=endpoint)

        # Get a fragment iterator to retrieve media
        response = media_client.get_media(
            StreamName=KVS_STREAM_NAME,
            StartSelector={"StartSelectorType": "NOW"}  # Start from now and go back
        )

        # Stream the video payload
        video_bytes = b""
        for event in response["Payload"]:
            if "PayloadFragment" in event:
                video_bytes += event["PayloadFragment"]["PayloadData"].read()

        if video_bytes:
            print(f"Retrieved {len(video_bytes)} bytes from KVS")
            return video_bytes

        print("No video data retrieved from KVS")
        return None

    except Exception as e:
        print(f"Error retrieving frame buffer from KVS: {e}")
        return None


def encode_clip(video_bytes, filename):
    """
    Encode video frames to MP4 using FFmpeg.
    Returns {filename, s3_url} or None on failure.
    """
    try:
        # Write to /tmp for processing
        input_file = f"/tmp/{uuid.uuid4()}.h264"
        output_file = f"/tmp/{filename}"

        with open(input_file, "wb") as f:
            f.write(video_bytes)

        # FFmpeg command: H.264 video → MP4 container
        cmd = [
            "ffmpeg",
            "-i", input_file,
            "-c:v", "copy",  # Copy codec (already H.264)
            "-f", "mp4",
            "-movflags", "+faststart",  # Web-ready
            "-y",  # Overwrite
            output_file,
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            print(f"FFmpeg error: {result.stderr.decode()}")
            return None

        # Upload to S3
        with open(output_file, "rb") as f:
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=f"replays/{filename}",
                Body=f.read(),
                ContentType="video/mp4",
                ACL="public-read",
            )

        # Generate S3 URL
        s3_url = f"https://{S3_BUCKET}.s3.{REGION}.amazonaws.com/replays/{filename}"

        # Cleanup
        import os as os_module
        try:
            os_module.remove(input_file)
            os_module.remove(output_file)
        except:
            pass

        print(f"Clip uploaded to S3: {s3_url}")
        return {"filename": filename, "s3_url": s3_url}

    except Exception as e:
        print(f"Error encoding clip: {e}")
        return None


def save_clip_metadata(clip_id, filename, s3_url, action="button"):
    """Save clip metadata to DynamoDB"""
    try:
        timestamp = int(datetime.utcnow().timestamp() * 1000)
        table.put_item(
            Item={
                "clip_id": clip_id,
                "timestamp": timestamp,
                "filename": filename,
                "s3_url": s3_url,
                "action": action,
                "created_at": datetime.utcnow().isoformat(),
                "ttl": int(datetime.utcnow().timestamp()) + (90 * 24 * 3600),  # 90 days TTL
            }
        )
        print(f"Metadata saved: {clip_id}")
        return True
    except Exception as e:
        print(f"Error saving metadata: {e}")
        return False


def get_clips_from_db(limit=20):
    """Query DynamoDB for recent clips"""
    try:
        response = table.scan(Limit=limit)
        clips = response.get("Items", [])
        # Sort by timestamp descending
        clips.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return clips
    except Exception as e:
        print(f"Error querying clips: {e}")
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ROUTES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def route_health(headers, path):
    """GET /health — Status check"""
    clips_count = len(get_clips_from_db(limit=1000))
    return http_response(
        200,
        {
            "status": "ok",
            "stream": KVS_STREAM_NAME,
            "clips": clips_count,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


def route_trigger(headers, body):
    """POST /trigger — Create clip from button press"""
    if not auth_check(headers):
        return http_response(401, {"error": "Unauthorized"})

    try:
        print("[trigger] Button press received")

        # Retrieve video from Kinesis
        video_bytes = get_frame_buffer_from_kvs()
        if not video_bytes:
            print("[trigger] No video data from Kinesis")
            return http_response(503, {"error": "No video data available"})

        # Generate filename and clip ID
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        clip_id = f"replay_{ts}_{uuid.uuid4().hex[:8]}"
        filename = f"{clip_id}.mp4"

        # Encode and upload
        result = encode_clip(video_bytes, filename)
        if not result:
            print("[trigger] Encoding failed")
            return http_response(500, {"error": "Encoding failed"})

        # Save metadata
        save_clip_metadata(clip_id, result["filename"], result["s3_url"], action="button")

        print(f"[trigger] Clip created: {clip_id}")
        return http_response(200, {
            "clip_id": clip_id,
            "s3_url": result["s3_url"],
            "filename": result["filename"],
        })

    except Exception as e:
        print(f"[trigger] Error: {e}")
        return http_response(500, {"error": str(e)})


def route_clips(headers, query_string):
    """GET /clips — Return metadata for all clips"""
    try:
        clips = get_clips_from_db(limit=100)
        return http_response(200, {
            "clips": clips,
            "count": len(clips),
        })
    except Exception as e:
        return http_response(500, {"error": str(e)})


def route_index(headers):
    """GET / — Serve web UI"""
    clips = get_clips_from_db(limit=20)

    # Build HTML
    clip_html = ""
    if clips:
        for i, clip in enumerate(clips):
            timestamp = datetime.fromtimestamp(clip["timestamp"] / 1000).strftime("%b %d · %H:%M:%S")
            s3_url = clip["s3_url"]
            filename = clip.get("filename", "replay.mp4")

            clip_html += f"""
            <article class="clip-card">
              <div class="video-wrap">
                <video controls playsinline preload="metadata" src="{s3_url}"></video>
                <div class="badge">Replay #{len(clips) - i}</div>
              </div>
              <div class="clip-body">
                <div>
                  <h3>Point replay</h3>
                  <p>{timestamp}</p>
                </div>
                <div class="clip-actions">
                  <a class="btn secondary" href="{s3_url}" target="_blank">Open</a>
                  <a class="btn secondary" href="{s3_url}" download="{filename}">Download</a>
                  <button class="btn primary" onclick="shareClip('{s3_url}')">Share</button>
                </div>
              </div>
            </article>
            """
    else:
        clip_html = """
        <section class="empty">
          <div class="empty-card">
            <h3>No replays yet</h3>
            <p>Press the court button to save a replay. Your first clip will appear here.</p>
          </div>
        </section>
        """

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

    <section class="grid">
      {clip_html}
    </section>
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
    """Main Lambda handler for API Gateway"""
    http_method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    headers = event.get("headers", {})
    body = event.get("body", "")
    query_string = event.get("rawQueryString", "")

    print(f"[handler] {http_method} {path}")

    # Route dispatch
    if path == "/" and http_method == "GET":
        return route_index(headers)
    elif path == "/health" and http_method == "GET":
        return route_health(headers, path)
    elif path == "/trigger" and http_method == "POST":
        return route_trigger(headers, body)
    elif path == "/clips" and http_method == "GET":
        return route_clips(headers, query_string)
    else:
        return http_response(404, {"error": "Not found"})
