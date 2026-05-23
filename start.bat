@echo off
title Padel Replay

echo.
echo  ================================================
echo   PADEL REPLAY - Starting all services
echo  ================================================
echo.

for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if "%%a"=="LAMBDA_URL"  set LAMBDA_URL=%%b
    if "%%a"=="EC2_URL"     set EC2_URL=%%b
)

pip install -r "C:\padel-replay\app\requirements.txt" --quiet 2>nul

REM 1. Mosquitto - minimized
start /MIN "Mosquitto" "C:\Program Files\mosquitto\mosquitto.exe" -c "C:\Program Files\mosquitto\mosquitto.conf"
timeout /t 2 /nobreak > nul

REM 2. Zigbee2MQTT
start "Zigbee2MQTT" powershell -NoExit -Command "cd 'C:\padel-replay\zigbee2mqtt'; pnpm start"
timeout /t 5 /nobreak > nul

REM 3. Stream relay -- re-encodes camera RTSP and pushes RTMP to EC2
start "Stream Relay" powershell -NoExit -Command "cd 'C:\padel-replay\app'; python stream_relay.py"
timeout /t 2 /nobreak > nul

REM 4. Button trigger -- calls EC2 /save on button press
start "Button + Clips" powershell -NoExit -Command "cd 'C:\padel-replay\app'; python mqtt_trigger.py"

echo.
echo  All services launched.
echo.
echo  Stream Relay  ^> watch for: "Connecting to camera..."
echo  Button+Clips  ^> watch for: clip URL after each button press
echo.
if defined EC2_URL (
    echo  EC2 health:  %EC2_URL%/health
)
if defined LAMBDA_URL (
    echo  Web UI:      %LAMBDA_URL%
)
echo.
echo  To stop everything: stop.bat
echo.
pause
