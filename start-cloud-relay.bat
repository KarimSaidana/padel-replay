@echo off
title Padel Replay - Cloud Relay

echo Starting Cloud Relay Services...
echo (Mosquitto + Zigbee2MQTT must already be running)
echo.

REM Install Python dependencies (fast, skips already-installed)
pip install -r C:\padel-replay\app\requirements.txt --quiet

REM Stream camera RTSP to AWS Kinesis Video Streams
REM Requires Docker Desktop to be running
start "1 - Camera Stream to KVS" powershell -NoExit -Command "cd 'C:\padel-replay\aws'; .\start_kvs_stream.ps1"

timeout /t 3 /nobreak > nul

REM Listen for Zigbee button and call Lambda to save clips
start "2 - MQTT Trigger Relay" powershell -NoExit -Command "cd 'C:\padel-replay\app'; python mqtt_trigger.py"

echo.
echo Both services launched.
echo.
echo  Window 1: Camera stream to AWS Kinesis (keeps 30s cloud buffer live)
echo  Window 2: MQTT trigger  (calls Lambda when court button is pressed)
echo.
echo Web UI: check LAMBDA_URL in your .env
echo.
pause
