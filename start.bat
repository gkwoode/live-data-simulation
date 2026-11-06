@echo off
rem Double-click to start LiveSim (runs start.ps1 for this session only, without changing system policy).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
pause
