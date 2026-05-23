# Padel Replay - AWS Deployment Script (PowerShell / Windows)
# Run from the aws/ directory: cd aws; .\deploy.ps1

param(
    [string]$StackName = "padel-replay",
    [string]$EnvName   = "padel-replay",
    [string]$Region    = ""
)

# Suppress Python SSL warnings printed to stderr by AWS CLI
$env:PYTHONWARNINGS = "ignore::urllib3.exceptions.InsecureRequestWarning"
$env:PYTHONUTF8 = "1"

# Read region from .env
$envFile = Join-Path (Join-Path $PSScriptRoot "..") ".env"
if (-not $Region) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*AWS_REGION\s*=\s*(.+)$') { $Region = $Matches[1].Trim() }
    }
    if (-not $Region) { $Region = "eu-central-1" }
}

function Step { param([string]$msg) Write-Host "`n--- $msg ---" -ForegroundColor Cyan }
function OK   { param([string]$msg) Write-Host "[OK] $msg" -ForegroundColor Green }
function INFO { param([string]$msg) Write-Host "[..] $msg" -ForegroundColor Gray }
function Fail { param([string]$msg) Write-Host "[ERR] $msg" -ForegroundColor Red; exit 1 }

# Run an AWS CLI command silencing the urllib3 warning line from stderr
function aw {
    $out = aws --no-verify-ssl @args 2>&1
    $clean = $out | Where-Object { $_ -notmatch "InsecureRequestWarning" -and $_ -notmatch "urllib3" }
    return $clean
}

# ---- Preflight ---------------------------------------------------------------
Step "Checking prerequisites"

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) { Fail "AWS CLI not found. Install from https://aws.amazon.com/cli/" }

INFO "Verifying AWS credentials..."
$idJson = aw sts get-caller-identity --output json
$identity = $idJson | ConvertFrom-Json
if (-not $identity.Account) { Fail "AWS credentials not working. Run: aws configure" }
OK "AWS account: $($identity.Account) | Region: $Region"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "Docker not found. Install Docker Desktop." }
INFO "Checking Docker engine..."
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Open Docker Desktop and wait for Engine running, then retry." }
OK "Docker engine is running"

# ---- Generate auth token -----------------------------------------------------
Step "Generating Lambda auth token"
$chars = (48..57) + (65..90) + (97..122)
$AUTH_TOKEN = -join ($chars | Get-Random -Count 48 | ForEach-Object { [char]$_ })
OK "Auth token generated"

# ---- Build FFmpeg Lambda layer -----------------------------------------------
Step "Building FFmpeg Lambda layer (this takes ~2 minutes)"
INFO "Pulling Amazon Linux 2 image and compiling FFmpeg..."

docker run --rm `
    -v "${PSScriptRoot}:/work" `
    -w /work `
    amazonlinux:2 `
    bash -c "yum install -y xz wget tar zip > /dev/null 2>&1 && bash build_ffmpeg_layer.sh"

if ($LASTEXITCODE -ne 0) { Fail "FFmpeg layer build failed inside Docker container" }

$layerZip = Join-Path $PSScriptRoot "layer_package\ffmpeg-layer.zip"
if (-not (Test-Path $layerZip)) { Fail "Layer zip not found at $layerZip after build" }
$layerSizeMB = [math]::Round((Get-Item $layerZip).Length / 1MB, 1)
OK "FFmpeg layer built - $layerSizeMB MB"

# ---- Publish FFmpeg layer ----------------------------------------------------
Step "Publishing FFmpeg layer to AWS Lambda"
INFO "Uploading via S3 staging bucket (avoids proxy issues with large uploads)..."

# Create a temporary staging bucket for deployment artifacts
$STAGING_BUCKET = "padel-deploy-$($identity.Account)-$Region"
aws --no-verify-ssl s3 mb "s3://$STAGING_BUCKET" --region $Region 2>&1 | Out-Null

INFO "Uploading $layerSizeMB MB to S3 staging bucket..."
aws --no-verify-ssl s3 cp $layerZip "s3://$STAGING_BUCKET/ffmpeg-layer.zip" --region $Region 2>&1 | Where-Object { $_ -notmatch "InsecureRequestWarning" -and $_ -notmatch "urllib3" } | Write-Host
if ($LASTEXITCODE -ne 0) { Fail "Failed to upload layer zip to S3" }

INFO "Publishing Lambda layer from S3..."
$LAYER_ARN = (aw lambda publish-layer-version `
    --layer-name padel-ffmpeg `
    --content "S3Bucket=$STAGING_BUCKET,S3Key=ffmpeg-layer.zip" `
    --compatible-runtimes python3.11 `
    --region $Region `
    --query "LayerVersionArn" `
    --output text) -join ""

if (-not $LAYER_ARN -or $LAYER_ARN -notmatch "^arn:") { Fail "Failed to publish Lambda layer. Got: $LAYER_ARN" }
OK "Layer ARN: $LAYER_ARN"

# ---- Package Lambda code -----------------------------------------------------
Step "Packaging Lambda function code"

$lambdaZip  = Join-Path $PSScriptRoot "lambda_package.zip"
$watermark  = Join-Path (Join-Path $PSScriptRoot "..") "watermark.png"
$handlerSrc = Join-Path $PSScriptRoot "lambda_handler.py"

if (Test-Path $lambdaZip) { Remove-Item $lambdaZip -Force }

$filesToZip = @($handlerSrc)
if (Test-Path $watermark) {
    $wmDest = Join-Path $PSScriptRoot "watermark.png"
    Copy-Item $watermark $wmDest -Force
    $filesToZip += $wmDest
    INFO "Watermark included"
}

Compress-Archive -Path $filesToZip -DestinationPath $lambdaZip -CompressionLevel Optimal
$pkgSizeKB = [math]::Round((Get-Item $lambdaZip).Length / 1KB, 1)
OK "Lambda package ready - $pkgSizeKB KB"

# ---- Deploy CloudFormation ---------------------------------------------------
Step "Deploying CloudFormation stack '$StackName' in $Region"
INFO "Creating: Kinesis Video Stream, S3 bucket, DynamoDB, Lambda, API Gateway..."
INFO "This takes 2-4 minutes..."

aws --no-verify-ssl cloudformation deploy `
    --template-file (Join-Path $PSScriptRoot "cloudformation.yaml") `
    --stack-name $StackName `
    --parameter-overrides `
        "EnvironmentName=$EnvName" `
        "LambdaAuthToken=$AUTH_TOKEN" `
        "FFmpegLayerArn=$LAYER_ARN" `
    --capabilities CAPABILITY_IAM `
    --region $Region 2>&1 | Where-Object { $_ -notmatch "InsecureRequestWarning" -and $_ -notmatch "urllib3" } | Write-Host

if ($LASTEXITCODE -ne 0) { Fail "CloudFormation deployment failed. Check AWS Console > CloudFormation for details." }
OK "Stack deployed"

# ---- Read stack outputs ------------------------------------------------------
Step "Reading stack outputs"

$outputsJson = (aw cloudformation describe-stacks `
    --stack-name $StackName `
    --region $Region `
    --query "Stacks[0].Outputs" `
    --output json) -join ""

$outputs      = $outputsJson | ConvertFrom-Json
$API_ENDPOINT = ($outputs | Where-Object { $_.OutputKey -eq "ApiEndpoint"       }).OutputValue
$STREAM_NAME  = ($outputs | Where-Object { $_.OutputKey -eq "KinesisStreamName" }).OutputValue
$S3_BUCKET    = ($outputs | Where-Object { $_.OutputKey -eq "S3BucketName"      }).OutputValue
$DYNAMO_TABLE = ($outputs | Where-Object { $_.OutputKey -eq "DynamoDBTable"     }).OutputValue

OK "API endpoint:   $API_ENDPOINT"
OK "KVS stream:     $STREAM_NAME"
OK "S3 bucket:      $S3_BUCKET"
OK "DynamoDB table: $DYNAMO_TABLE"

# ---- Upload Lambda code ------------------------------------------------------
Step "Uploading Lambda function code"

aw lambda update-function-code `
    --function-name "$EnvName-handler" `
    --zip-file "fileb://$lambdaZip" `
    --region $Region | Out-Null

if ($LASTEXITCODE -ne 0) { Fail "Failed to upload Lambda code" }
INFO "Waiting for Lambda to finish updating..."
Start-Sleep -Seconds 8
OK "Lambda code deployed"

# ---- Update .env -------------------------------------------------------------
Step "Updating .env"
$envContent = Get-Content $envFile -Raw
$envContent = $envContent -replace '(?m)^LAMBDA_URL=.*',        "LAMBDA_URL=$API_ENDPOINT"
$envContent = $envContent -replace '(?m)^LAMBDA_AUTH_TOKEN=.*', "LAMBDA_AUTH_TOKEN=$AUTH_TOKEN"
$envContent = $envContent -replace '(?m)^KVS_STREAM_NAME=.*',   "KVS_STREAM_NAME=$STREAM_NAME"
Set-Content $envFile $envContent -Encoding utf8 -NoNewline
OK ".env updated"

# ---- Smoke tests -------------------------------------------------------------
Step "Running smoke tests"

INFO "Test 1/3 - Lambda /health endpoint..."
try {
    $health = Invoke-RestMethod -Uri "$API_ENDPOINT/health" -Method GET -TimeoutSec 15
    if ($health.status -eq "ok") {
        OK "Health check passed - stream: $($health.stream), clips: $($health.clips)"
    } else {
        Write-Host "[WARN] Unexpected health body: $($health | ConvertTo-Json)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[WARN] Health check failed (Lambda cold start - try again in 10s): $_" -ForegroundColor Yellow
}

INFO "Test 2/3 - S3 bucket accessible..."
aw s3 ls "s3://$S3_BUCKET" --region $Region 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    OK "S3 bucket '$S3_BUCKET' is accessible"
} else {
    Write-Host "[WARN] S3 bucket check failed" -ForegroundColor Yellow
}

INFO "Test 3/3 - DynamoDB table status..."
$tableStatus = (aw dynamodb describe-table `
    --table-name $DYNAMO_TABLE `
    --region $Region `
    --query "Table.TableStatus" `
    --output text --cli-read-timeout 300 --cli-connect-timeout 60) -join ""

if ($tableStatus -eq "ACTIVE") {
    OK "DynamoDB table '$DYNAMO_TABLE' is ACTIVE"
} else {
    Write-Host "[WARN] DynamoDB status: $tableStatus" -ForegroundColor Yellow
}

# ---- Summary -----------------------------------------------------------------
Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "  DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host "  Web UI:      $API_ENDPOINT"
Write-Host "  KVS Stream:  $STREAM_NAME"
Write-Host "  S3 Bucket:   $S3_BUCKET"
Write-Host "  Region:      $Region"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Start camera stream:  .\start_kvs_stream.ps1"
Write-Host "  2. Start MQTT trigger:   cd ..\app; python mqtt_trigger.py"
Write-Host "  3. Open web UI:          $API_ENDPOINT"
Write-Host "=============================================" -ForegroundColor Green




