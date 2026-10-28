@echo off
REM ---------------------------------------------------------------------------
REM Open ONLY the MusicGang window (no bot, no Lavalink).
REM
REM Use this when a friend is hosting the bot: put their Tailscale IP in .env
REM as MUSICGANG_HOST, then double-click this. To host yourself, use start.bat.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
    echo Python not found. Install it from python.org with "Add to PATH" checked.
    pause
    exit /b 1
)
python -c "import webview" >nul 2>nul || python -m pip install --quiet --disable-pip-version-check pywebview python-dotenv
start "" pythonw "musicgang_app.pyw"
