# ---------------------------------------------------------------------------
# Update this folder to the latest version from GitHub. No git, no GitHub
# account needed: it downloads the public ZIP and copies the files over.
#
# Your .env, Lavalink.jar and plugins\ are not in the ZIP, so they are kept.
# Run via update.bat (double-click). Close the MusicGang window first.
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
$dest = $PSScriptRoot
$zip = Join-Path $env:TEMP 'musicgang_update.zip'
$tmp = Join-Path $env:TEMP 'musicgang_update'

Write-Host "Downloading latest version..."
Invoke-WebRequest 'https://github.com/akabinsaleh/Discord_bot/archive/refs/heads/main.zip' -OutFile $zip

Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive $zip -DestinationPath $tmp -Force
$src = Get-ChildItem $tmp | Select-Object -First 1   # the Discord_bot-main folder

Copy-Item (Join-Path $src.FullName '*') $dest -Recurse -Force
Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Updated. Installing any new Python packages..."
python -m pip install --quiet --disable-pip-version-check -r (Join-Path $dest 'requirements.txt')
Write-Host "Done. Open MusicGang again." -ForegroundColor Green
