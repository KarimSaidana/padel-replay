@echo off
title Padel Replay - Stop

echo Stopping all Padel Replay services...

taskkill /F /IM mosquitto.exe  >nul 2>&1
taskkill /F /IM node.exe       >nul 2>&1
taskkill /F /IM python.exe     >nul 2>&1
taskkill /F /IM ffmpeg.exe     >nul 2>&1

echo Done.
pause
