@echo off
title Padel Replay System Launcher

echo Starting Padel Replay System...
echo.

REM Start Mosquitto MQTT
start "1 - Mosquitto MQTT" powershell -NoExit -Command "cd 'C:\Program Files\mosquitto'; .\mosquitto.exe -c .\mosquitto.conf -v"

REM Wait a little for Mosquitto to start
timeout /t 3 /nobreak > nul

REM Start Zigbee2MQTT
start "2 - Zigbee2MQTT" powershell -NoExit -Command "cd 'C:\padel-replay\zigbee2mqtt'; pnpm start"

REM Wait a little for Zigbee2MQTT to start
timeout /t 5 /nobreak > nul

REM Install Python dependencies (safe to run every time, skips already-installed)
pip install -r C:\padel-replay\app\requirements.txt --quiet

REM Start Replay App
start "3 - Padel Replay App" powershell -NoExit -Command "cd 'C:\padel-replay\app'; python server.py"

echo.
echo All services launched.
echo.
echo Open the replay feed here:
echo http://localhost:3000
echo.
pause