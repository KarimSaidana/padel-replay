# Padel Replay

Instant highlights for your padel court. Press a button, save the last 30 seconds of video.

## Overview

Padel Replay is a court-side instant replay system that lets players review great rallies or disputed points with a single button press. The system continuously buffers video from an IP camera and saves clips to a web-accessible feed.

## Features

- **One-button capture** — Wireless Zigbee button on the court triggers a 30-second replay
- **Zero-latency buffer** — Keeps a rolling frame buffer; no cold-start delay
- **Web UI** — Browse, watch, and share clips in a beautiful responsive interface
- **Cloud backup** — Optional S3 integration to keep clips in the cloud
- **Watermarking** — Automatically overlay your court branding on replays

## Architecture

```
IP Camera (RTSP)
    ↓
recorder.py (OpenCV, rolling buffer)
    ↓
server.py (Flask web server)
    ↓
MQTT (Zigbee2MQTT button events)
    ↓
FFmpeg (re-encode, watermark, compress)
    ↓
S3 (optional cloud backup)
```

## Quick Start

### Prerequisites

- Python 3.9+
- FFmpeg
- MQTT broker (Mosquitto)
- Zigbee2MQTT (optional, for wireless button)
- IP camera with RTSP stream

### Installation

1. Clone and install dependencies:
   ```bash
   git clone https://github.com/yourusername/padel-replay.git
   cd padel-replay
   pip install -r app/requirements.txt
   ```

2. Configure `.env`:
   ```env
   RTSP_URL=rtsp://user:pass@camera-ip:554/stream1
   MQTT_URL=mqtt://localhost:1883
   BUTTON_TOPIC=zigbee2mqtt/padel_button
   S3_BUCKET=your-bucket
   S3_REGION=us-east-1
   S3_ACCESS_KEY_ID=your-key
   S3_SECRET_ACCESS_KEY=your-secret
   ```

3. Start the system:
   ```bash
   python app/server.py
   ```

   The web UI will be at `http://localhost:3000`

### Windows All-in-One

Run `start-padel-replay.bat` to launch all services (Mosquitto, Zigbee2MQTT, Python app).

## API

- `GET /` — Web UI with clip feed
- `GET /clips/<filename>` — Serve a clip
- `GET /trigger` — Manually trigger a replay (for testing)
- `GET /health` — Health check with recorder status

## File Structure

```
padel-replay/
├── app/
│   ├── server.py          # Flask web server & MQTT listener
│   ├── recorder.py        # Camera buffer & clip recording
│   └── requirements.txt
├── clips/                 # Saved MP4 clips (local)
├── watermark.png          # Optional branding overlay
├── .env                   # Configuration (git-ignored)
└── zigbee2mqtt/           # Zigbee2MQTT bridge (submodule)
```

## Development

### Camera Connection Issues?

- Check RTSP URL: `ffmpeg -rtsp_transport tcp -i rtsp://...`
- Verify firewall allows RTSP (port 554)
- Check recorder status at `/health`

### Button Not Triggering?

- Verify MQTT is running: `mosquitto_sub -h localhost -t "#"`
- Check Zigbee2MQTT logs for button events
- Verify `BUTTON_TOPIC` matches your device name

## License

MIT

## Credits

Built for padel courts with ❤️
