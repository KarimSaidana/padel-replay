# Padel Replay - AWS Deployment Script
# Run from the aws/ directory: cd aws; .\deploy.ps1

param(
    [string]$StackName = "padel-replay",
    [string]$EnvName   = "padel-replay",
    [string]$Region    = ""
)

$env:PYTHONWARNINGS = "ignore::urllib3.exceptions.InsecureRequestWarning"
$env:PYTHONUTF8 = "1"

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

function aw {
    $out = aws --no-verify-ssl @args 2>&1
    return $out | Where-Object { $_ -notmatch "InsecureRequestWarning" -and $_ -notmatch "urllib3" }
}

# ---- Preflight ---------------------------------------------------------------
Step "Checking prerequisites"
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) { Fail "AWS CLI not found." }
$idJson   = (aw sts get-caller-identity --output json) -join ""
$identity = $idJson | ConvertFrom-Json
if (-not $identity.Account) { Fail "AWS credentials not working. Run: aws configure" }
OK "AWS account: $($identity.Account) | Region: $Region"

# ---- Generate auth tokens ----------------------------------------------------
Step "Generating auth tokens"
$chars = (48..57) + (65..90) + (97..122)
$LAMBDA_TOKEN   = -join ($chars | Get-Random -Count 48 | ForEach-Object { [char]$_ })
$RECORDER_TOKEN = -join ($chars | Get-Random -Count 48 | ForEach-Object { [char]$_ })
OK "Tokens generated"

# ---- Package Lambda code -----------------------------------------------------
Step "Packaging Lambda function"
$lambdaZip  = Join-Path $PSScriptRoot "lambda_package.zip"
$handlerSrc = Join-Path $PSScriptRoot "lambda_handler.py"
if (Test-Path $lambdaZip) { Remove-Item $lambdaZip -Force }
Compress-Archive -Path $handlerSrc -DestinationPath $lambdaZip -CompressionLevel Optimal
$kb = [math]::Round((Get-Item $lambdaZip).Length / 1KB, 1)
OK "Lambda package: $kb KB"

# ---- Deploy CloudFormation ---------------------------------------------------
Step "Deploying CloudFormation stack '$StackName'"
INFO "Creating/updating: S3, DynamoDB, Lambda, API Gateway, EC2 recorder..."
INFO "EC2 instance creation takes 3-5 minutes..."

aws --no-verify-ssl cloudformation deploy `
    --template-file (Join-Path $PSScriptRoot "cloudformation.yaml") `
    --stack-name $StackName `
    --parameter-overrides `
        "EnvironmentName=$EnvName" `
        "LambdaAuthToken=$LAMBDA_TOKEN" `
        "RecorderAuthToken=$RECORDER_TOKEN" `
    --capabilities CAPABILITY_IAM `
    --region $Region 2>&1 | Where-Object { $_ -notmatch "InsecureRequestWarning" -and $_ -notmatch "urllib3" } | Write-Host

if ($LASTEXITCODE -ne 0) { Fail "CloudFormation deployment failed." }
OK "Stack deployed"

# ---- Read stack outputs ------------------------------------------------------
Step "Reading stack outputs"
$outputsJson = (aw cloudformation describe-stacks `
    --stack-name $StackName `
    --region $Region `
    --query "Stacks[0].Outputs" `
    --output json) -join ""
$outputs      = $outputsJson | ConvertFrom-Json
$API_ENDPOINT = ($outputs | Where-Object { $_.OutputKey -eq "ApiEndpoint"      }).OutputValue
$S3_BUCKET    = ($outputs | Where-Object { $_.OutputKey -eq "S3BucketName"     }).OutputValue
$DYNAMO_TABLE = ($outputs | Where-Object { $_.OutputKey -eq "DynamoDBTable"    }).OutputValue
$RECORDER_IP  = ($outputs | Where-Object { $_.OutputKey -eq "RecorderPublicIP" }).OutputValue

OK "API endpoint:   $API_ENDPOINT"
OK "S3 bucket:      $S3_BUCKET"
OK "DynamoDB table: $DYNAMO_TABLE"
OK "Recorder IP:    $RECORDER_IP"

# ---- Upload Lambda code ------------------------------------------------------
Step "Uploading Lambda code"
aw lambda update-function-code `
    --function-name "$EnvName-handler" `
    --zip-file "fileb://$lambdaZip" `
    --region $Region | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Lambda upload failed" }
Start-Sleep -Seconds 5
OK "Lambda deployed"

# ---- Deploy cloud_recorder.py to EC2 via SSM ---------------------------------
Step "Deploying cloud_recorder.py to EC2"

# Get EC2 instance ID
$instanceId = (aw ec2 describe-instances `
    --filters "Name=tag:Name,Values=$EnvName-recorder" "Name=instance-state-name,Values=running" `
    --query "Reservations[0].Instances[0].InstanceId" `
    --region $Region `
    --output text) -join ""

if (-not $instanceId -or $instanceId -eq "None") {
    INFO "EC2 instance not running yet. Waiting up to 3 minutes..."
    $waited = 0
    while ($waited -lt 180) {
        Start-Sleep -Seconds 15
        $waited += 15
        $instanceId = (aw ec2 describe-instances `
            --filters "Name=tag:Name,Values=$EnvName-recorder" "Name=instance-state-name,Values=running" `
            --query "Reservations[0].Instances[0].InstanceId" `
            --region $Region `
            --output text) -join ""
        if ($instanceId -and $instanceId -ne "None") { break }
        INFO "Still waiting... ($waited s)"
    }
}
if (-not $instanceId -or $instanceId -eq "None") { Fail "EC2 instance not found/running after 3 minutes" }
OK "Instance: $instanceId"

# Upload cloud_recorder.py to S3 staging
$STAGING_BUCKET = "padel-deploy-$($identity.Account)-$Region"
aws --no-verify-ssl s3 mb "s3://$STAGING_BUCKET" --region $Region 2>&1 | Out-Null
$recorderSrc = Join-Path $PSScriptRoot ".." | Join-Path -ChildPath "app\cloud_recorder.py"
$recorderSrc = (Resolve-Path $recorderSrc).Path
aws --no-verify-ssl s3 cp $recorderSrc "s3://$STAGING_BUCKET/cloud_recorder.py" --region $Region 2>&1 | `
    Where-Object { $_ -notmatch "InsecureRequestWarning" -and $_ -notmatch "urllib3" } | Out-Null
OK "Uploaded cloud_recorder.py to S3 staging"

# Wait for SSM agent to be ready
INFO "Waiting for SSM agent on EC2..."
$ssmReady = $false
for ($i = 0; $i -lt 20; $i++) {
    $pingStatus = (aw ssm describe-instance-information `
        --filters "Key=InstanceIds,Values=$instanceId" `
        --region $Region `
        --query "InstanceInformationList[0].PingStatus" `
        --output text) -join ""
    if ($pingStatus -eq "Online") { $ssmReady = $true; break }
    Start-Sleep -Seconds 15
    INFO "SSM not ready yet... ($([int]($i+1)*15)s)"
}
if (-not $ssmReady) { Fail "SSM agent not responding after 5 minutes" }
OK "SSM agent online"

# Run deploy commands on EC2
INFO "Copying cloud_recorder.py and starting service..."
$ssmCommands = @(
    "aws s3 cp s3://$STAGING_BUCKET/cloud_recorder.py /opt/padel-recorder/cloud_recorder.py --region $Region",
    "systemctl start padel-recorder || systemctl restart padel-recorder",
    "systemctl status padel-recorder --no-pager"
)
$cmdId = (aw ssm send-command `
    --instance-ids $instanceId `
    --document-name "AWS-RunShellScript" `
    --parameters "commands=$($ssmCommands | ConvertTo-Json -Compress)" `
    --region $Region `
    --query "Command.CommandId" `
    --output text) -join ""

if (-not $cmdId) { Fail "SSM send-command failed" }

# Poll until command completes
$done = $false
for ($i = 0; $i -lt 24; $i++) {
    Start-Sleep -Seconds 5
    $status = (aw ssm get-command-invocation `
        --command-id $cmdId `
        --instance-id $instanceId `
        --region $Region `
        --query "Status" `
        --output text) -join ""
    if ($status -eq "Success") { $done = $true; break }
    if ($status -eq "Failed")  { Fail "SSM command failed on EC2" }
}
if (-not $done) { Fail "SSM command timed out" }
OK "cloud_recorder.py deployed and service started"

# ---- Update .env -------------------------------------------------------------
Step "Updating .env"
$envContent = Get-Content $envFile -Raw
$EC2_URL        = "http://${RECORDER_IP}:5000"
$EC2_STREAM_URL = "rtmp://${RECORDER_IP}:1935/live/stream"

$envContent = $envContent -replace '(?m)^LAMBDA_URL=.*',          "LAMBDA_URL=$API_ENDPOINT"
$envContent = $envContent -replace '(?m)^LAMBDA_AUTH_TOKEN=.*',   "LAMBDA_AUTH_TOKEN=$LAMBDA_TOKEN"
$envContent = $envContent -replace '(?m)^RECORDER_AUTH_TOKEN=.*', "RECORDER_AUTH_TOKEN=$RECORDER_TOKEN"
$envContent = $envContent -replace '(?m)^EC2_URL=.*',             "EC2_URL=$EC2_URL"
$envContent = $envContent -replace '(?m)^EC2_STREAM_URL=.*',      "EC2_STREAM_URL=$EC2_STREAM_URL"
$envContent = $envContent -replace '(?m)^EC2_RTMP_URL=.*',        "EC2_STREAM_URL=$EC2_STREAM_URL"
$envContent = $envContent -replace '(?m)^S3_BUCKET=.*',           "S3_BUCKET=$S3_BUCKET"

foreach ($pair in @("RECORDER_AUTH_TOKEN=$RECORDER_TOKEN","EC2_URL=$EC2_URL","EC2_STREAM_URL=$EC2_STREAM_URL")) {
    $key = $pair.Split("=")[0]
    if ($envContent -notmatch "(?m)^$key=") {
        $envContent = $envContent.TrimEnd() + "`n$pair`n"
    }
}
Set-Content $envFile $envContent -Encoding utf8 -NoNewline
OK ".env updated"

# ---- Smoke tests -------------------------------------------------------------
Step "Running smoke tests"

INFO "Test 1/2 - Lambda web UI..."
try {
    $r = Invoke-RestMethod -Uri "$API_ENDPOINT/health" -Method GET -TimeoutSec 15
    if ($r.status -eq "ok") { OK "Lambda healthy - clips: $($r.clips)" }
    else { Write-Host "[WARN] Unexpected Lambda response" -ForegroundColor Yellow }
} catch { Write-Host "[WARN] Lambda health check failed (cold start): $_" -ForegroundColor Yellow }

INFO "Test 2/2 - EC2 recorder..."
Start-Sleep -Seconds 5
try {
    $r = Invoke-RestMethod -Uri "$EC2_URL/health" -Method GET -TimeoutSec 10
    OK "EC2 recorder healthy - connected: $($r.connected), buffer: $($r.buffer_seconds)s"
} catch { Write-Host "[WARN] EC2 health check failed (service still starting): $_" -ForegroundColor Yellow }

# ---- Summary -----------------------------------------------------------------
Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "  DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host "  Web UI:       $API_ENDPOINT"
Write-Host "  EC2 Recorder: $EC2_URL"
Write-Host "  EC2 Stream:   $EC2_STREAM_URL"
Write-Host "  S3 Bucket:    $S3_BUCKET"
Write-Host ""
Write-Host "Next step: start.bat"
Write-Host "=============================================" -ForegroundColor Green
