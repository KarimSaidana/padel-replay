# Padel Replay - AWS Deployment Guide

## Prerequisites

- AWS Account with CLI access
- `aws` CLI installed and configured
- `git` installed
- Python 3.11+
- Bash or similar shell

## Step-by-Step Deployment

### 1. Clone & Setup

```bash
git clone https://github.com/KarimSaidana/padel-replay.git
cd padel-replay/aws
```

### 2. Build FFmpeg Lambda Layer

This creates a Lambda layer with FFmpeg pre-compiled:

```bash
# Make script executable
chmod +x build_ffmpeg_layer.sh

# Run the build
./build_ffmpeg_layer.sh
```

This downloads FFmpeg and creates `layer_package/ffmpeg-layer.zip` (~500MB).

### 3. Publish FFmpeg Layer to AWS

```bash
LAYER_ARN=$(aws lambda publish-layer-version \
  --layer-name padel-ffmpeg \
  --zip-file fileb://layer_package/ffmpeg-layer.zip \
  --compatible-runtimes python3.11 \
  --query 'LayerVersionArn' \
  --output text)

echo "FFmpeg Layer ARN: $LAYER_ARN"
```

Save this ARN for the next step.

### 4. Choose Auth Token

Generate a secure random token for Lambda-to-local authentication:

```bash
# macOS/Linux
LAMBDA_AUTH_TOKEN=$(openssl rand -hex 32)

# Windows PowerShell
$LAMBDA_AUTH_TOKEN = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | % {[char]$_})

echo "Auth Token: $LAMBDA_AUTH_TOKEN"
```

Save this token - you'll need it in `.env`.

### 5. Deploy CloudFormation Stack

```bash
aws cloudformation deploy \
  --template-file cloudformation.yaml \
  --stack-name padel-replay \
  --parameter-overrides \
    EnvironmentName=padel-replay \
    LambdaAuthToken="$LAMBDA_AUTH_TOKEN" \
    FFmpegLayerArn="$LAYER_ARN" \
  --capabilities CAPABILITY_IAM \
  --region us-east-1
```

Wait for the stack to complete (2-3 minutes).

### 6. Get Stack Outputs

```bash
aws cloudformation describe-stacks \
  --stack-name padel-replay \
  --query 'Stacks[0].Outputs' \
  --region us-east-1
```

You'll see:
- `ApiEndpoint`: Your Lambda API URL
- `KinesisStreamName`: Kinesis stream name
- `S3BucketName`: S3 bucket for clips
- `DynamoDBTable`: DynamoDB table name

### 7. Upload Lambda Function Code

The CloudFormation template created a Lambda function with placeholder code. Now replace it with the actual handler:

```bash
# Package the Lambda handler
zip lambda_handler.zip lambda_handler.py

# Update the function
aws lambda update-function-code \
  --function-name padel-replay-handler \
  --zip-file fileb://lambda_handler.zip \
  --region us-east-1
```

### 8. Configure Local Machine

On your court machine, update `.env`:

```env
# MQTT Configuration
MQTT_URL=mqtt://localhost:1883
BUTTON_TOPIC=zigbee2mqtt/padel_button

# Lambda Configuration
LAMBDA_URL=https://xxxxx.lambda-url.us-east-1.on.aws
LAMBDA_AUTH_TOKEN=your-secret-token-here

# Kinesis Configuration
KVS_STREAM_NAME=padel-replay-stream
```

Replace:
- `LAMBDA_URL` with the `ApiEndpoint` from step 6
- `LAMBDA_AUTH_TOKEN` with the token from step 4

### 9. Configure Kinesis Camera Stream

You need to get your camera's RTSP stream into Kinesis. There are two options:

**Option A: Local Producer (Recommended for local cameras)**

```bash
# On the court machine, create a producer that sends camera stream to Kinesis:
ffmpeg -i rtsp://user:pass@camera-ip:554/stream1 \
  -c:v libx264 -preset ultrafast -b:v 1000k \
  -f flv "rtmps://kinesis.us-east-1.amazonaws.com:443/put-media?stream-arn=arn:aws:kinesisvideo:us-east-1:ACCOUNT_ID:stream/padel-replay-stream/1"
```

**Option B: AWS Elemental Live / Third-party encoder**

Use a hardware encoder or AWS service to push RTSP to Kinesis.

### 10. Test the Setup

```bash
# On local machine, test Lambda health
curl -H "Authorization: Bearer $LAMBDA_AUTH_TOKEN" \
  https://xxxxx.lambda-url.us-east-1.on.aws/health

# Response should be:
# {"status": "ok", "stream": "padel-replay-stream", "clips": 0}
```

### 11. Start Local Services

```bash
# Install local dependencies
pip install -r ../app/requirements.txt

# Start Mosquitto and Zigbee2MQTT (if not already running)
./start-padel-replay.bat  # Windows
# or
bash ../start-padel-replay.sh  # macOS/Linux

# Start MQTT trigger
cd ../app
python mqtt_trigger.py
```

### 12. Test End-to-End

1. Press the Zigbee button on the court
2. Check `mqtt_trigger.py` logs - should show "Button pressed"
3. Check Lambda logs:
   ```bash
   aws logs tail /aws/lambda/padel-replay-handler --follow
   ```
4. Verify clip appears in S3:
   ```bash
   aws s3 ls s3://padel-replay-clips-ACCOUNT_ID/replays/
   ```
5. Check DynamoDB:
   ```bash
   aws dynamodb scan --table-name padel-replay-clips
   ```
6. Open web UI: Visit your `LAMBDA_URL` in browser

## Monitoring

### CloudWatch Logs

View Lambda execution logs:
```bash
aws logs tail /aws/lambda/padel-replay-handler --follow
```

### Kinesis Stream Metrics

Check if camera is streaming to Kinesis:
```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/Kinesis \
  --metric-name GetRecords.IteratorAgeMilliseconds \
  --dimensions Name=StreamName,Value=padel-replay-stream \
  --statistics Average \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300
```

### S3 Bucket

List clips:
```bash
aws s3 ls s3://padel-replay-clips-ACCOUNT_ID/replays/ --human-readable --summarize
```

## Troubleshooting

### Lambda Timeout (>120s)

- Increase timeout in `cloudformation.yaml` (LambdaFunction > Timeout)
- Check Kinesis stream health
- Verify FFmpeg is working on Lambda

### Kinesis No Data

- Verify camera RTSP works: `ffmpeg -i rtsp://...`
- Check IAM role has Kinesis permissions
- Verify stream endpoint is correct

### Clips Not Appearing in S3

1. Check Lambda logs for errors
2. Verify S3 bucket exists and is writable
3. Check IAM role S3 permissions
4. Monitor DynamoDB for metadata entries

### High AWS Costs

- Kinesis: Reduce stream retention (currently 24h)
- Lambda: Lower memory if not needed (currently 2GB)
- S3: Enable lifecycle policies to move old clips to Glacier

## Cleanup

To delete all AWS resources:

```bash
# Delete S3 bucket contents first
aws s3 rm s3://padel-replay-clips-ACCOUNT_ID/replays/ --recursive

# Delete CloudFormation stack
aws cloudformation delete-stack --stack-name padel-replay

# Delete Lambda layer
aws lambda delete-layer-version --layer-name padel-ffmpeg --version-number 1
```

## Support

For issues, check:
1. AWS CloudWatch Logs
2. Lambda function configuration
3. Local `.env` file (LAMBDA_AUTH_TOKEN, LAMBDA_URL)
4. Camera RTSP connectivity
5. MQTT broker running (Mosquitto)
