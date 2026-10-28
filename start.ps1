# ---------------------------------------------------------------------------
# MusicGang launcher.
#
# Starts the two processes the bot needs, in order, and makes the setup
# self-repairing. Run it via start.bat (double-click).
#
#   1. Dependencies - reinstalled every launch. Upgrading Python silently
#      leaves the new interpreter without PyNaCl, which breaks voice while the
#      bot still looks healthy. Reinstalling makes that unfixable-looking bug
#      impossible instead of merely fixed once.
#   2. Lavalink     - the audio server. Started only if not already up, then
#      WAITED for. It needs several seconds before it accepts connections, and
#      a bot that connects too early just fails.
#   3. bot.py       - launched in its own window with an auto-restart loop.
# ---------------------------------------------------------------------------

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }

Say "=== MusicGang launcher ===" Cyan

# --- 1. Locate Python ------------------------------------------------------
# Resolve the real interpreter path rather than trusting a bare "python" alias,
# so we install dependencies into the exact interpreter that will run the bot.
$pyExe = $null
foreach ($probe in @(@('py', '-3'), @('python'))) {
    $cmd = Get-Command $probe[0] -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
        $args = @($probe[1..($probe.Length - 1)]) + @('-c', 'import sys; print(sys.executable)')
        $found = & $cmd.Source @args 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { $pyExe = $found.Trim(); break }
    } catch { }
}
if (-not $pyExe) {
    Say "ERROR: Python not found on PATH. Install Python, then re-run this." Red
    exit 1
}
Say "Python: $pyExe"

# --- 2. Self-heal dependencies --------------------------------------------
Say "Checking dependencies (this is what stops PyNaCl going missing again)..."
# Fast path: if every library imports, skip pip entirely (saves ~3-5s per
# launch). pip only runs when something is actually missing or broken.
& $pyExe -c 'import discord, wavelink, dotenv, nacl, davey' 2>$null
if ($LASTEXITCODE -eq 0) {
    Say "Dependencies OK" Green
} else {
    Say "Installing missing dependencies..."
    & $pyExe -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Say "ERROR: dependency install failed. Fix the pip error above and re-run." Red
        exit 1
    }
    Say "Dependencies installed" Green
}

# --- 3. Locate Java --------------------------------------------------------
$javaExe = (Get-Command java -ErrorAction SilentlyContinue).Source
if (-not $javaExe) {
    $javaExe = (Get-ChildItem 'C:\Program Files\Java' -Filter java.exe -Recurse -ErrorAction SilentlyContinue |
                Select-Object -First 1).FullName
}
if (-not $javaExe) {
    Say "ERROR: Java not found. Lavalink needs Java to run." Red
    exit 1
}

# --- 4. Start Lavalink (only if it isn't already up) -----------------------
$lava = Get-CimInstance Win32_Process -Filter "Name='java.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*Lavalink.jar*' }
if ($lava) {
    Say "Lavalink already running (PID $($lava.ProcessId))" Green
} else {
    Say "Starting Lavalink..."
    # --enable-native-access: Lavalink loads a native UDP library; Java 24+ warns
    # about that on every start and will refuse it outright in a future release.
    Start-Process -FilePath $javaExe -ArgumentList '--enable-native-access=ALL-UNNAMED', '-jar', 'Lavalink.jar' -WorkingDirectory $PSScriptRoot -WindowStyle Minimized
}

# Wait for Lavalink to actually accept connections. Guessing a fixed delay is
# how you get a bot that starts before its audio server is listening.
Say "Waiting for Lavalink to accept connections..."
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    if (Get-NetTCPConnection -LocalPort 2333 -State Listen -ErrorAction SilentlyContinue) { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    Say "ERROR: Lavalink never opened port 2333. Check its window for the error." Red
    exit 1
}
Say "Lavalink ready (took ~${i}s)" Green

# --- 5. Start the bot ------------------------------------------------------
# Check for the restart loop (run_bot.ps1) as well as bot.py itself: between a
# crash and the loop's 5s restart there is no bot.py process, and a launch in
# that gap would otherwise start a second loop = two bots on one token.
$bot = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
       Where-Object { $_.CommandLine -like '*bot.py*' -or $_.CommandLine -like '*run_bot.ps1*' }
if ($bot) {
    # Two bots on one token double-answer every command, which looks like the
    # bot "glitching" - refuse rather than create that confusing mess.
    Say "Bot already running (PID $($bot.ProcessId)) - not starting a second copy." Yellow
} else {
    Say "Starting MusicGang..."
    # The script path must be quoted: -ArgumentList joins its items with spaces
    # and quotes nothing, so a folder name containing a space (this one does)
    # would otherwise arrive as two broken arguments.
    $runner = '"{0}"' -f (Join-Path $PSScriptRoot 'run_bot.ps1')
    Start-Process powershell -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', $runner
    )
}

Say ""
Say "Both parts are up. Wait for 'Slash commands synced' in the MusicGang window," Cyan
Say "then use /play in Discord. Keep both windows open." Cyan
