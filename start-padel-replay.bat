@echo off
title Padel Replay - Local Services

echo Starting Padel Replay Local Services...
echo.

REM Start Mosquitto MQTT
start "1 - Mosquitto MQTT" powershell -NoExit -Command "cd 'C:\Program Files\mosquitto'; .\mosquitto.exe -c .\mosquitto.conf -v"

timeout /t 3 /nobreak > nul

REM Start Zigbee2MQTT (Zigbee button bridge)
start "2 - Zigbee2MQTT" powershell -NoExit -Command "cd 'C:\padel-replay\zigbee2mqtt'; pnpm start"

timeout /t 5 /nobreak > nul

REM Install Python dependencies
pip install -r C:\padel-replay\app\requirements.txt --quiet

REM Stream camera RTSP → AWS Kinesis Video Streams (keeps 30-second cloud buffer live)
REM Requires Docker Desktop to be running
start "3 - Camera Stream → KVS" powershell -NoExit -Command "cd 'C:\padel-replay\aws'; .\start_kvs_stream.ps1"

timeout /t 3 /nobreak > nul

REM Start MQTT trigger relay (listens for button, calls Lambda)
start "4 - MQTT Trigger Relay" powershell -NoExit -Command "cd 'C:\padel-replay\app'; python mqtt_trigger.py"

echo.
echo All services launched.
echo.
echo  Window 1: Mosquitto MQTT broker
echo  Window 2: Zigbee2MQTT  (Zigbee button bridge)
echo  Window 3: Camera stream to AWS Kinesis (requires Docker)
echo  Window 4: MQTT trigger  (calls Lambda on button press)
echo.
echo Open the replay feed at the LAMBDA_URL in your .env
echo.
pause
