@echo off
title Padel Replay - Local

echo.
echo  ================================================
echo   PADEL REPLAY - LOCAL MODE
echo   Camera recorded directly on this PC
echo  ================================================
echo.

for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if "%%a"=="LAMBDA_URL"  set LAMBDA_URL=%%b
)

pip install -r "C:\padel-replay\app\requirements.txt" --quiet 2>nul

REM 1. Mosquitto - minimized
start /MIN "Mosquitto" "C:\Program Files\mosquitto\mosquitto.exe" -c "C:\Program Files\mosquitto\mosquitto.conf"
timeout /t 2 /nobreak > nul

REM 2. Zigbee2MQTT
start "Zigbee2MQTT" powershell -NoExit -Command "cd 'C:\padel-replay\zigbee2mqtt'; pnpm start"
timeout /t 5 /nobreak > nul

REM 3. Local recorder - reads camera directly, saves segments, serves /save on :5000
start "Local Recorder" powershell -NoExit -Command "cd 'C:\padel-replay\app'; python local_recorder.py"
timeout /t 3 /nobreak > nul

REM 4. Button trigger - calls localhost:5000/save (EC2_URL overridden here)
start "Button Trigger" powershell -NoExit -Command "$env:EC2_URL='http://localhost:5000'; cd 'C:\padel-replay\app'; python mqtt_trigger.py"

echo.
echo  All services launched.
echo.
echo  Recorder health: http://localhost:5000/health
if defined LAMBDA_URL (
    echo  Web UI:          %LAMBDA_URL%
)
echo.
echo  Wait ~35s for the buffer to fill before pressing the button.
echo  To stop everything: stop.bat
echo.
pause
