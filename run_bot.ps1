# ---------------------------------------------------------------------------
# Runs bot.py and restarts it if it stops. Launched by start.ps1 in its own
# window; you can also run it directly.
#
# A dropped network connection or an unhandled error shouldn't mean the bot is
# silently gone until someone notices /play failing hours later - so restart it.
#
# But restarting is only right for transient faults. A bad token or a missing
# dependency fails instantly and identically forever, and an unconditional loop
# would spin on it, burying the error in a scroll of restarts. So: if the bot
# dies quickly several times running, stop and leave the reason on screen.
# ---------------------------------------------------------------------------

# -Python: the interpreter start.ps1 resolved and installed packages into.
# Probing PATH here again could pick a different Python (one without
# discord.py), so prefer what we're given.
param([string]$Python)

Set-Location -LiteralPath $PSScriptRoot

$pyExe = $null
if ($Python -and (Test-Path -LiteralPath $Python)) { $pyExe = $Python }
foreach ($probe in @(@('py', '-3'), @('python'))) {
    if ($pyExe) { break }
    $cmd = Get-Command $probe[0] -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
        $args = @($probe[1..($probe.Length - 1)]) + @('-c', 'import sys; print(sys.executable)')
        $found = & $cmd.Source @args 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { $pyExe = $found.Trim(); break }
    } catch { }
}
if (-not $pyExe) {
    Write-Host "ERROR: Python not found on PATH." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

$quickFails = 0

while ($true) {
    $startedAt = Get-Date
    & $pyExe bot.py
    $ranFor = ((Get-Date) - $startedAt).TotalSeconds

    # Under 15s means it never really got going - it failed at startup.
    if ($ranFor -lt 15) { $quickFails++ } else { $quickFails = 0 }

    if ($quickFails -ge 3) {
        Write-Host ""
        Write-Host "MusicGang failed to start 3 times in a row." -ForegroundColor Red
        Write-Host "This is a real error, not a blip - read the message above." -ForegroundColor Red
        Write-Host "Not restarting, so the reason stays on screen." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }

    Write-Host ""
    Write-Host "MusicGang stopped after $([int]$ranFor)s. Restarting in 5s (Ctrl+C to quit)..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}
