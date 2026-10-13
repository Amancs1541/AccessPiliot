#Requires -Version 5.1
<#
  AccessPilot - one script to install, start, and (on later runs) just start the app again.

  First run:  checks for Docker (installs Docker Desktop via winget if missing), builds everything
              fresh from GitHub, waits for the backend to come up, then prints the one-time admin
              password and opens the dashboard.
  Later runs: skips all of that and just starts the already-built containers - fast.

  Usage:
    ./run.ps1              start (first time: full install+build; after that: fast start)
    ./run.ps1 -Rebuild      pull the latest code from GitHub and rebuild, even on a later run
    ./run.ps1 -Stop         stop the app, keep all data
    ./run.ps1 -Reset        stop the app and WIPE all data (new admin password next start)
#>
[CmdletBinding()]
param(
    [switch]$Rebuild,
    [switch]$Stop,
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
$markerFile = '.accesspilot-installed'

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Info($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    $msg" -ForegroundColor Red }

# ---------------------------------------------------------------- shortcuts: stop / reset

if ($Stop) {
    Write-Step "Stopping AccessPilot (data kept)"
    docker compose stop
    exit 0
}
if ($Reset) {
    Write-Warn "This deletes the database and the stored credential-encryption key."
    Write-Warn "You will get a NEW one-time admin password next time you start it."
    $confirm = Read-Host "Type YES to continue"
    if ($confirm -ne 'YES') { Write-Info "Cancelled - nothing changed."; exit 0 }
    docker compose down -v
    Remove-Item $markerFile -ErrorAction SilentlyContinue
    Write-Ok "Stopped and wiped. Run this script again to start fresh."
    exit 0
}

# ---------------------------------------------------------------- 1. Docker installed?

Write-Step "Checking for Docker"
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Write-Warn "Docker is not installed on this machine."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Info "Installing Docker Desktop via winget - this can take a few minutes..."
        winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
        Write-Warn "Docker Desktop is installed. Windows may still ask to enable WSL2 and reboot - do that if asked."
        Write-Warn "Then start Docker Desktop from the Start menu, wait until it says 'Docker Desktop is running',"
        Write-Warn "and run this script again."
    } else {
        Write-Err "winget is not available either. Install Docker Desktop by hand:"
        Write-Err "  https://www.docker.com/products/docker-desktop/"
        Write-Err "Then run this script again."
    }
    exit 1
}
Write-Ok "Docker is installed ($(docker --version))"

# ---------------------------------------------------------------- 2. Docker daemon running?

Write-Step "Checking Docker is running"
function Test-DockerUp { docker info *> $null; return ($LASTEXITCODE -eq 0) }

if (-not (Test-DockerUp)) {
    Write-Info "Docker Desktop is not running yet - starting it..."
    $desktopExe = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $desktopExe) {
        Start-Process $desktopExe | Out-Null
        Write-Info "Waiting for Docker to come up (can take a minute the first time)..."
        $waited = 0
        while ($waited -lt 120 -and -not (Test-DockerUp)) {
            Start-Sleep -Seconds 3
            $waited += 3
            Write-Host "." -NoNewline
        }
        Write-Host ""
    }
    if (-not (Test-DockerUp)) {
        Write-Err "Docker still is not responding. Start Docker Desktop yourself, wait for 'running', then re-run this script."
        exit 1
    }
}
Write-Ok "Docker is running"

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Err "Docker Compose is not available. Reinstalling Docker Desktop usually fixes this (it bundles Compose)."
    exit 1
}
Write-Ok "Docker Compose is available"

# ---------------------------------------------------------------- 3. First run vs. already installed

$firstRun = -not (Test-Path $markerFile)

if ($firstRun -or $Rebuild) {
    if ($firstRun) { Write-Step "First time - downloading and building AccessPilot from GitHub" }
    else { Write-Step "Pulling the latest code and rebuilding" }
    docker compose up -d --build
    if ($LASTEXITCODE -ne 0) { Write-Err "Build/start failed - see the error above."; exit 1 }
} else {
    Write-Step "Starting AccessPilot"
    docker compose up -d
    if ($LASTEXITCODE -ne 0) { Write-Err "Start failed - see the error above."; exit 1 }
}

# ---------------------------------------------------------------- 4. Wait for the backend

Write-Step "Waiting for the backend to be ready"
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 3
    Write-Host "." -NoNewline
}
Write-Host ""
if ($ready) { Write-Ok "Backend is up" }
else { Write-Warn "Still not responding after 3 minutes - check: docker compose logs -f backend" }

# ---------------------------------------------------------------- 5. First-time onboarding

if ($firstRun) {
    New-Item -ItemType File -Path $markerFile -Force | Out-Null
    Write-Step "First-time setup"
    Write-Host ""
    Write-Host "  AccessPilot is ready at:  http://localhost:5173" -ForegroundColor Cyan

    $bootstrapLine = (docker compose logs backend 2>$null) | Select-String "Bootstrap login"
    if ($bootstrapLine) {
        $match = [regex]::Match($bootstrapLine.Line, "password:\s*(\S+)")
        if ($match.Success) {
            Write-Host "  First sign-in  ->  username: admin   password: $($match.Groups[1].Value)" -ForegroundColor Cyan
            Write-Warn "That password is shown ONLY ONCE - copy it now."
        }
    } else {
        Write-Warn "Could not find the one-time password yet. Run:  docker compose logs backend | findstr Bootstrap"
    }

    Write-Host ""
    Write-Host "  After signing in, AccessPilot's own setup wizard walks you through connecting your" -ForegroundColor DarkGray
    Write-Host "  Microsoft Entra tenant. When registering the app in Entra, add this as a Single-page" -ForegroundColor DarkGray
    Write-Host "  application redirect URI:  http://localhost:5173" -ForegroundColor DarkGray
    Write-Host ""

    try { Start-Process "http://localhost:5173" } catch {}
} else {
    Write-Ok "AccessPilot is running at http://localhost:5173"
}

Write-Host ""
Write-Info "Useful commands:"
Write-Info "  ./run.ps1           start it again any time (fast - no rebuild)"
Write-Info "  ./run.ps1 -Rebuild  pull the latest code from GitHub and rebuild"
Write-Info "  ./run.ps1 -Stop     stop it, keep all data"
Write-Info "  ./run.ps1 -Reset    wipe everything and start over"
Write-Info "  docker compose logs -f backend   watch backend logs"
