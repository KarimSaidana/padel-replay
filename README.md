# Padel Replay

Instant highlights for your padel court. Press a button, save the last 30 seconds of video. **Now fully serverless on AWS.**

## Overview

Padel Replay is a court-side instant replay system that lets players review great rallies or disputed points with a single button press. All video processing and storage happens in the cloud (AWS Lambda, S3, Kinesis), keeping your local machine lightweight and lag-free.

## Features

- **One-button capture** — Wireless Zigbee button on the court triggers a 30-second replay
- **Serverless architecture** — Everything runs on AWS (Lambda, Kinesis, S3, DynamoDB)
- **Zero local lag** — Your PC stays responsive (just a lightweight MQTT relay)
- **Web UI** — Browse, watch, and share clips at your Lambda endpoint
- **Automatic scaling** — Handle multiple courts/cameras without scaling local hardware
- **Clip metadata** — Search and filter clips by date/time

## Architecture

```
IP Camera (RTSP on Local Network)
    ↓
AWS Kinesis Video Streams (24-hour rolling buffer)
    ↓
Lambda (triggered by button press)
    ├─ Extracts 30s from Kinesis
    ├─ Re-encodes to MP4 with FFmpeg
    ├─ Uploads to S3
    └─ Stores metadata in DynamoDB
    ↓
S3 (public-read video storage + CDN)
    ↓
Web UI (Lambda API Gateway) — https://your-lambda-url.on.aws
    ↓
Local Machine: MQTT Listener Only (minimal CPU/memory)
```

## Deployment

### Prerequisites

- AWS Account with credentials configured
- Camera with RTSP stream
- Zigbee2MQTT bridge + Mosquitto (local machine)
- Python 3.11+

### Step 1: Deploy AWS Infrastructure

```bash
cd aws
bash build_ffmpeg_layer.sh  # Build FFmpeg Lambda layer

aws lambda publish-layer-version \
  --layer-name padel-ffmpeg \
  --zip-file fileb://layer_package/ffmpeg-layer.zip \
  --compatible-runtimes python3.11

# Deploy CloudFormation stack
aws cloudformation deploy \
  --template-file cloudformation.yaml \
  --stack-name padel-replay \
  --parameter-overrides \
    EnvironmentName=padel-replay \
    LambdaAuthToken=your-secret-token-here \
    FFmpegLayerArn=arn:aws:lambda:REGION:ACCOUNT:layer:padel-ffmpeg:1 \
  --capabilities CAPABILITY_IAM
```

Get the outputs:
```bash
aws cloudformation describe-stacks \
  --stack-name padel-replay \
  --query 'Stacks[0].Outputs'
```

### Step 2: Configure Local Machine

1. Update `.env`:
   ```env
   MQTT_URL=mqtt://localhost:1883
   BUTTON_TOPIC=zigbee2mqtt/padel_button
   LAMBDA_URL=https://xxxxx.lambda-url.us-east-1.on.aws
   LAMBDA_AUTH_TOKEN=your-secret-token-here
   ```

2. Install dependencies:
   ```bash
   pip install -r app/requirements.txt
   ```

3. Update Lambda handler code:
   ```bash
   # Replace placeholder in CloudFormation with actual lambda_handler.py
   # Then update the Lambda function:
   aws lambda update-function-code \
     --function-name padel-replay-handler \
     --zip-file fileb://lambda_handler.zip
   ```

### Step 3: Start Local Services

```bash
# Windows
start-padel-replay.bat

# macOS/Linux
bash start-padel-replay.sh
```

## API Endpoints

All endpoints require `Authorization: Bearer {LAMBDA_AUTH_TOKEN}` header.

- `GET /` — Web UI with clip feed (public)
- `POST /trigger` — Create clip from button press
- `GET /clips` — List recent clips with metadata
- `GET /health` — System status

## Local Machine Requirements

Your court machine now only runs:
- **Mosquitto** — MQTT broker (handles button events)
- **Zigbee2MQTT** — Bridges Zigbee button to MQTT
- **mqtt_trigger.py** — ~50MB Python process that listens for button presses and calls Lambda

**Memory usage**: <100MB (vs ~1.2GB for local recorder)
**CPU usage**: <5% (vs 60% during encoding)

## File Structure

```
padel-replay/
├── app/
│   ├── mqtt_trigger.py    # Local MQTT listener → Lambda caller
│   ├── requirements.txt    # Minimal deps: paho-mqtt, requests
│   └── recorder.py         # (archived - no longer used)
├── aws/
│   ├── cloudformation.yaml # AWS infrastructure (Kinesis, S3, DynamoDB, Lambda)
│   ├── lambda_handler.py   # Lambda function (deployed to AWS)
│   ├── build_ffmpeg_layer.sh # FFmpeg layer builder
│   └── DEPLOY.md           # Detailed deployment guide
├── watermark.png           # (Optional) Overlay on clips
├── .env                    # Configuration (git-ignored)
└── zigbee2mqtt/            # Zigbee2MQTT bridge (submodule)
```

## Troubleshooting

### Button not triggering clips?

1. Check MQTT connection:
   ```bash
   mosquitto_sub -h localhost -t "#"
   ```
   Should see button events.

2. Check Lambda auth token:
   ```bash
   echo $LAMBDA_AUTH_TOKEN  # Should be set
   ```

3. Check Lambda logs:
   ```bash
   aws logs tail /aws/lambda/padel-replay-handler --follow
   ```

### No clips in Kinesis?

1. Verify camera RTSP is accessible:
   ```bash
   ffmpeg -i rtsp://user:pass@camera-ip/stream -t 5 -c copy /dev/null
   ```

2. Check Kinesis stream:
   ```bash
   aws kinesisvideo describe-stream --stream-name padel-replay-stream
   ```

### Clips not appearing in S3?

1. Check Lambda execution logs
2. Verify S3 bucket permissions
3. Check DynamoDB for clip metadata entries

## Costs

**Typical usage (1 clip/hour, ~100 clips/month)**:
- Kinesis Video Streams: ~$10-15/month (24h retention)
- Lambda: ~$0.50/month (1M requests free tier)
- S3: ~$2-5/month (storage + transfer)
- DynamoDB: <$1/month (on-demand, well within free tier)
- **Total**: ~$15-25/month

vs running local PC: electricity + hardware wear

## License

MIT

## Credits

Built for padel courts with ❤️
