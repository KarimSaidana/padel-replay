@echo off
title Padel Replay - Local MQTT Relay

echo Starting Padel Replay Local Services...
echo.

REM Start Mosquitto MQTT (if not already running)
start "1 - Mosquitto MQTT" powershell -NoExit -Command "cd 'C:\Program Files\mosquitto'; .\mosquitto.exe -c .\mosquitto.conf -v"

REM Wait a little for Mosquitto to start
timeout /t 3 /nobreak > nul

REM Start Zigbee2MQTT (button bridge)
start "2 - Zigbee2MQTT" powershell -NoExit -Command "cd 'C:\padel-replay\zigbee2mqtt'; pnpm start"

REM Wait a little for Zigbee2MQTT to start
timeout /t 5 /nobreak > nul

REM Install Python dependencies
pip install -r C:\padel-replay\app\requirements.txt --quiet

REM Start Local MQTT Trigger (connects to Lambda)
start "3 - MQTT Trigger Relay" powershell -NoExit -Command "cd 'C:\padel-replay\app'; python mqtt_trigger.py"

echo.
echo All services launched.
echo.
echo This machine is now a lightweight relay for:
echo - Zigbee button (MQTT events)
echo - Lambda clip creation (via MQTT trigger)
echo.
echo Open the replay feed at the LAMBDA_URL from your .env
echo.
pause