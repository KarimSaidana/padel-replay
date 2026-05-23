# Padel Replay — AWS Deployment Script (PowerShell / Windows)
# Run from the aws/ directory: cd aws; .\deploy.ps1
#
# Prerequisites:
#   - AWS CLI installed and configured (aws configure)
#   - Docker Desktop (for building FFmpeg layer on Amazon Linux)
#   - Bash available (Git Bash, WSL, or Cygwin) for build_ffmpeg_layer.sh

param(
    [string]$StackName  = "padel-replay",
    [string]$EnvName    = "padel-replay",
    [string]$Region     = "us-east-1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ────────────────────────────────────────────────────────────────
function Step { param([string]$msg) Write-Host "`n--- $msg ---" -ForegroundColor Cyan }
function OK   { param([string]$msg) Write-Host "OK  $msg" -ForegroundColor Green }
function Fail { param([string]$msg) Write-Host "ERR $msg" -ForegroundColor Red; exit 1 }

# ── Preflight ──────────────────────────────────────────────────────────────
Step "Checking prerequisites"

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Fail "AWS CLI not found. Install from https://aws.amazon.com/cli/"
}

$identity = aws sts get-caller-identity --output json 2>&1 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { Fail "AWS credentials not configured. Run: aws configure" }
OK "AWS identity: $($identity.Account)"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
}
docker info > $null 2>&1
if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Start Docker Desktop and retry." }
OK "Docker is running"

# ── Generate auth token ────────────────────────────────────────────────────
Step "Generating Lambda auth token"
$chars = (48..57) + (65..90) + (97..122)
$AUTH_TOKEN = -join ($chars | Get-Random -Count 48 | ForEach-Object { [char]$_ })
OK "Token generated (save this — you'll need it in .env)"

# ── Build FFmpeg Lambda layer ──────────────────────────────────────────────
Step "Building FFmpeg Lambda layer via Docker (Amazon Linux 2)"

# Use Docker to build the layer so the binary matches Lambda's runtime
docker run --rm `
    -v "${PSScriptRoot}:/work" `
    -w /work `
    amazonlinux:2 `
    bash -c "yum install -y xz wget tar > /dev/null 2>&1 && bash build_ffmpeg_layer.sh"

if ($LASTEXITCODE -ne 0) { Fail "FFmpeg layer build failed" }

$layerZip = Join-Path $PSScriptRoot "layer_package\ffmpeg-layer.zip"
if (-not (Test-Path $layerZip)) { Fail "Layer zip not found at $layerZip" }
OK "FFmpeg layer built"

# ── Publish FFmpeg layer ───────────────────────────────────────────────────
Step "Publishing FFmpeg Lambda layer"

$LAYER_ARN = aws lambda publish-layer-version `
    --layer-name padel-ffmpeg `
    --zip-file "fileb://$layerZip" `
    --compatible-runtimes python3.11 `
    --region $Region `
    --query "LayerVersionArn" `
    --output text

if ($LASTEXITCODE -ne 0) { Fail "Failed to publish Lambda layer" }
OK "Layer ARN: $LAYER_ARN"

# ── Package Lambda code (with watermark) ──────────────────────────────────
Step "Packaging Lambda function"

$lambdaZip = Join-Path $PSScriptRoot "lambda_package.zip"
$watermark  = Join-Path $PSScriptRoot ".." "watermark.png"

if (Test-Path $lambdaZip) { Remove-Item $lambdaZip }

# Use Compress-Archive to bundle handler + watermark
$filesToZip = @((Join-Path $PSScriptRoot "lambda_handler.py"))
if (Test-Path $watermark) {
    Copy-Item $watermark (Join-Path $PSScriptRoot "watermark.png") -Force
    $filesToZip += (Join-Path $PSScriptRoot "watermark.png")
}

Compress-Archive -Path $filesToZip -DestinationPath $lambdaZip -CompressionLevel Optimal
OK "Lambda package ready"

# ── Deploy CloudFormation stack ────────────────────────────────────────────
Step "Deploying CloudFormation stack ($StackName)"

aws cloudformation deploy `
    --template-file (Join-Path $PSScriptRoot "cloudformation.yaml") `
    --stack-name $StackName `
    --parameter-overrides `
        "EnvironmentName=$EnvName" `
        "LambdaAuthToken=$AUTH_TOKEN" `
        "FFmpegLayerArn=$LAYER_ARN" `
    --capabilities CAPABILITY_IAM `
    --region $Region

if ($LASTEXITCODE -ne 0) { Fail "CloudFormation deployment failed" }
OK "Stack deployed"

# ── Get stack outputs ──────────────────────────────────────────────────────
Step "Reading stack outputs"

$outputs = aws cloudformation describe-stacks `
    --stack-name $StackName `
    --region $Region `
    --query "Stacks[0].Outputs" `
    --output json | ConvertFrom-Json

$API_ENDPOINT = ($outputs | Where-Object { $_.OutputKey -eq "ApiEndpoint"     }).OutputValue
$STREAM_NAME  = ($outputs | Where-Object { $_.OutputKey -eq "KinesisStreamName" }).OutputValue
$S3_BUCKET    = ($outputs | Where-Object { $_.OutputKey -eq "S3BucketName"    }).OutputValue

# ── Upload Lambda code ─────────────────────────────────────────────────────
Step "Uploading Lambda function code"

aws lambda update-function-code `
    --function-name "$EnvName-handler" `
    --zip-file "fileb://$lambdaZip" `
    --region $Region | Out-Null

if ($LASTEXITCODE -ne 0) { Fail "Failed to upload Lambda code" }
OK "Lambda code deployed"

# ── Update .env ────────────────────────────────────────────────────────────
$envFile = Join-Path $PSScriptRoot ".." ".env"
$envContent = Get-Content $envFile -Raw -ErrorAction SilentlyContinue
if ($envContent) {
    $envContent = $envContent -replace 'LAMBDA_URL=.*',       "LAMBDA_URL=$API_ENDPOINT"
    $envContent = $envContent -replace 'LAMBDA_AUTH_TOKEN=.*', "LAMBDA_AUTH_TOKEN=$AUTH_TOKEN"
    $envContent = $envContent -replace 'KVS_STREAM_NAME=.*',   "KVS_STREAM_NAME=$STREAM_NAME"
    Set-Content $envFile $envContent -Encoding utf8
    OK ".env updated automatically"
}

# ── Summary ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host " DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host " Web UI:           $API_ENDPOINT"
Write-Host " KVS Stream:       $STREAM_NAME"
Write-Host " S3 Bucket:        $S3_BUCKET"
Write-Host " Lambda Auth Token: $AUTH_TOKEN"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Your .env has been updated automatically."
Write-Host "  2. Start camera streaming: .\start_kvs_stream.ps1"
Write-Host "  3. Start MQTT trigger:     cd ..\app; python mqtt_trigger.py"
Write-Host "  4. Open the web UI:        $API_ENDPOINT"
Write-Host "=============================================" -ForegroundColor Green
