# Streams the court camera's RTSP feed into AWS Kinesis Video Streams.
# Requires Docker Desktop running on the court machine.
# Run this alongside mqtt_trigger.py — it keeps the 30-second cloud buffer live.

param(
    [string]$EnvFile = (Join-Path $PSScriptRoot ".." ".env")
)

# ── Load .env ──────────────────────────────────────────────────────────────
$env_vars = @{}
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*?)\s*=\s*(.*)\s*$') {
        $env_vars[$Matches[1]] = $Matches[2].Trim('"').Trim("'")
    }
}

$RTSP_URL         = $env_vars["RTSP_URL"]
$KVS_STREAM_NAME  = $env_vars["KVS_STREAM_NAME"]
$AWS_ACCESS_KEY   = $env_vars["AWS_ACCESS_KEY_ID"]
$AWS_SECRET_KEY   = $env_vars["AWS_SECRET_ACCESS_KEY"]
$AWS_REGION       = $env_vars["AWS_REGION"]

if (-not $RTSP_URL -or -not $KVS_STREAM_NAME -or -not $AWS_ACCESS_KEY) {
    Write-Error "Missing required .env variables: RTSP_URL, KVS_STREAM_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION"
    exit 1
}

Write-Host ""
Write-Host "=== Camera → Kinesis Video Streams ==="
Write-Host "RTSP URL:    $RTSP_URL"
Write-Host "KVS Stream:  $KVS_STREAM_NAME"
Write-Host "Region:      $AWS_REGION"
Write-Host "======================================="
Write-Host ""

# ── Check Docker ───────────────────────────────────────────────────────────
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
    exit 1
}

$dockerRunning = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker is not running. Start Docker Desktop and try again."
    exit 1
}

Write-Host "[kvs] Starting camera stream (press Ctrl+C to stop)..."

# ── Stream RTSP → KVS via official AWS SDK container ──────────────────────
# The container includes the KVS GStreamer plugin compiled for Amazon Linux.
# --network host gives the container access to the local camera network.
docker run --rm `
    --network host `
    -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY `
    -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_KEY `
    -e AWS_DEFAULT_REGION=$AWS_REGION `
    amazon/kinesis-video-streams-producer-sdk-cpp:latest `
    /kvssdk/kvs_gstreamer_sample $KVS_STREAM_NAME $RTSP_URL

if ($LASTEXITCODE -ne 0) {
    Write-Error "[kvs] Stream stopped with error code $LASTEXITCODE"
    exit $LASTEXITCODE
}
