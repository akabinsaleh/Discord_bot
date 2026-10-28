@echo off
REM Double-click to update this folder to the latest version from GitHub.
REM Keeps your .env, Lavalink.jar and plugins. Close MusicGang first.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"
echo.
pause
