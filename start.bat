@echo off
REM ---------------------------------------------------------------------------
REM Double-click this to start the music bot.
REM
REM Everything lives in start.ps1 - this wrapper just runs it, because
REM double-clicking a .ps1 opens Notepad instead of running it.
REM ---------------------------------------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
echo.
pause
