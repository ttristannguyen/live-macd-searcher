<#
.SYNOPSIS
    Pull a consistent snapshot of the droplet's live-macd-searcher database to this machine.

.DESCRIPTION
    Adapted from macd_searcher's scripts/sync_prod_db.ps1 — the same mechanism, on
    purpose (DESIGN §13): one way of doing backups for both apps on that droplet.

    Pull, not push: this desktop sits behind NAT with no inbound route, so the droplet
    cannot reach it without port-forwarding. Running the transfer from here reuses the
    outbound SSH that already works, and adds no new exposure.

    Three steps, and each one exists for a reason:

      1. Ask the droplet to make a snapshot via SQLite's Online Backup API. A plain scp of
         the live DB can miss the -wal file — the app writes in WAL mode every hour, so
         recent commits may not be in the main file yet. `.backup` is safe against a
         database being written to concurrently and yields one self-contained file.
      2. Download to a temp name, then move into place, so a half-transferred file never
         sits at the path where it would be read as a corrupt database.
      3. Verify and report freshness. A bar closes every hour, so a newest bar older than
         a few hours means the app on the droplet is unhealthy — worth knowing here
         rather than discovering it mid-analysis.

    Nothing is written on the droplet except the snapshot file, which is overwritten
    each run rather than accumulating.

.PARAMETER VmHost
    SSH target: `user@host`, or an alias from ~/.ssh/config. Required, and passed in
    rather than hardcoded so this script carries no host details into the repo.

.EXAMPLE
    .\scripts\sync_prod_db.ps1 -VmHost macd-vm
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$VmHost,

    [string]$RemoteDir = '~/live-macd-searcher',
    [string]$LocalDb = 'state\prod_snapshot.sqlite3'
)

$ErrorActionPreference = 'Stop'

# Resolve the project root from this script's own location, so it works from any
# working directory a scheduler hands it.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path 'logs')) { New-Item -ItemType Directory 'logs' | Out-Null }
if (-not (Test-Path 'state')) { New-Item -ItemType Directory 'state' | Out-Null }

$log = Join-Path $root 'logs\sync_prod_db.log'
function Write-Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

Write-Log "=== sync starting (host=$VmHost) ==="

if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Write-Log 'FAILED: ssh not on PATH. Install the Windows OpenSSH client or use Git Bash''s ssh.'
    exit 1
}

$remoteSnap = 'state/live_macd_searcher_snapshot.sqlite3'
$localTmp = "$LocalDb.part"

# --- 1. WAL-safe snapshot on the droplet ---------------------------------------
# The program travels base64-encoded, for the reasons macd_searcher's script records:
# PowerShell 5.1 strips embedded double quotes when handing a string to a native .exe
# (so `python -c "..."` arrives broken), and piping the program over stdin prepends a
# UTF-8 BOM that Python rejects. Base64 is [A-Za-z0-9+/=] only; nothing reinterprets it.
$py = @"
import sqlite3
src = sqlite3.connect('state/live_macd_searcher.sqlite3')
dst = sqlite3.connect('$remoteSnap')
with dst:
    src.backup(dst)
src.close()
dst.close()
print('snapshot ok')
"@
# Normalise CRLF before encoding so the payload is identical whatever wrote this file.
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($py -replace "`r", "")))
$remoteCmd = "cd $RemoteDir; echo $b64 | base64 -d | .venv/bin/python -"

Write-Log 'step 1/3: creating WAL-safe snapshot on the droplet'
$out = & ssh $VmHost $remoteCmd 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "FAILED: remote snapshot exited $LASTEXITCODE"
    Write-Log "  $out"
    exit 1
}
Write-Log "  remote said: $out"

# --- 2. Download, then move into place -----------------------------------------
Write-Log 'step 2/3: downloading'
if (Test-Path $localTmp) { Remove-Item $localTmp -Force }
& scp -q "${VmHost}:$RemoteDir/$remoteSnap" $localTmp 2>&1 | ForEach-Object { Write-Log "  $_" }
if ($LASTEXITCODE -ne 0) {
    Write-Log "FAILED: scp exited $LASTEXITCODE"
    exit 1
}
if (-not (Test-Path $localTmp)) {
    Write-Log 'FAILED: scp reported success but no file arrived'
    exit 1
}
Move-Item -Path $localTmp -Destination $LocalDb -Force
$mb = [math]::Round((Get-Item $LocalDb).Length / 1MB, 1)
Write-Log "  wrote $LocalDb ($mb MB)"

# --- 3. Verify + freshness check -----------------------------------------------
Write-Log 'step 3/3: verifying'
$verify = @"
import sqlite3, time
c = sqlite3.connect('file:$($LocalDb -replace '\\','/')?mode=ro', uri=True)
bars, symbols = c.execute('SELECT COUNT(*), COUNT(DISTINCT symbol) FROM bars').fetchone()
states = dict(c.execute('SELECT state, COUNT(*) FROM windows GROUP BY state').fetchall())
newest = c.execute('SELECT MAX(open_time) FROM bars').fetchone()[0]
age = (time.time() * 1000 - (newest + 3_600_000)) / 3_600_000  # since the newest bar closed
print(f'bars={bars} symbols={symbols} windows={states}')
print(f'newest bar closed {age:.1f}h ago')
# A bar closes every hour; much older than that and the droplet side is unhealthy.
print('STALE: newest bar is over 3h old - check the app on the droplet' if age > 3 else 'freshness ok')
"@
$verifyOut = & '.venv\Scripts\python.exe' -c $verify 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "FAILED: snapshot did not open cleanly - the download may be truncated"
    Write-Log "  $verifyOut"
    exit 1
}
$verifyOut | ForEach-Object { Write-Log "  $_" }

Write-Log '=== sync complete ==='
exit 0
