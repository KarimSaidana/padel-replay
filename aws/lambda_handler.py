"""
Padel Replay Lambda Handler - web UI and clip listing only.
All clip creation is handled by cloud_recorder.py on EC2.
"""
import json
import os
from datetime import datetime

import boto3

dynamodb       = boto3.resource("dynamodb")
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
table          = dynamodb.Table(DYNAMODB_TABLE)


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


def get_clips(limit=20):
    try:
        items = table.scan(Limit=limit).get("Items", [])
        for c in items:
            if "timestamp" in c:
                c["timestamp"] = int(c["timestamp"])
        items.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return items
    except Exception as e:
        print(f"[dynamo] Error: {e}")
        return []


def route_health():
    clips = get_clips(limit=1000)
    return http_response(200, {
        "status":    "ok",
        "clips":     len(clips),
        "timestamp": datetime.utcnow().isoformat(),
    })


def route_clips():
    clips = get_clips(limit=100)
    return http_response(200, {"clips": clips, "count": len(clips)})


def route_index():
    clips = get_clips(limit=20)

    if clips:
        cards = ""
        for i, clip in enumerate(clips):
            ts  = datetime.fromtimestamp(clip["timestamp"] / 1000).strftime("%b %d  %H:%M:%S")
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
        System live &middot; Auto-refresh 5s
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
    setInterval(() => {{
      const playing = [...document.querySelectorAll('video')].some(v => !v.paused);
      if (!playing) location.reload();
    }}, 10000);
    async function shareClip(url) {{
      try {{
        if (navigator.share) {{ await navigator.share({{ title: 'Padel Replay', url }}); return; }}
        await navigator.clipboard.writeText(url);
        alert('Link copied - paste on WhatsApp, Instagram, or anywhere.');
      }} catch {{
        try {{ await navigator.clipboard.writeText(url); alert('Link copied.'); }}
        catch {{ alert('Sharing not available.'); }}
      }}
    }}
  </script>
</body>
</html>"""
    return html_response(html)


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path   = event.get("rawPath", "/")
    print(f"[handler] {method} {path}")

    if path == "/" and method == "GET":
        return route_index()
    elif path == "/health":
        return route_health()
    elif path == "/clips":
        return route_clips()
    else:
        return http_response(404, {"error": "Not found"})
