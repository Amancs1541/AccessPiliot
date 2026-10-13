@echo off
REM Double-click this to run AccessPilot - it just hands off to run.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
pause
