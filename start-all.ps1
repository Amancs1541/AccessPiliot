# Starts the AccessPilot backend (FastAPI, port 8001) and frontend (Vite, port 5173) each in their own window.
$root = $PSScriptRoot

function Test-PortInUse($port) {
    return $null -ne (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

if (Test-PortInUse 8001) {
    Write-Host "Backend already running on port 8001 — skipping."
} else {
    try {
        Start-Process -FilePath powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001" -WindowStyle Normal -ErrorAction Stop
        Write-Host "Started backend: http://localhost:8001"
    } catch {
        Write-Host "FAILED to start backend: $_"
    }
}

if (Test-PortInUse 5173) {
    Write-Host "Frontend already running on port 5173 — skipping."
} else {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; npm run dev"
    Write-Host "Started frontend: http://localhost:5173"
}
