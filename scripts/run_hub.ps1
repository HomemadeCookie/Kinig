# Start the Kinig hub on Windows. Double-click scripts\run_hub.bat, or run:
#   powershell -ExecutionPolicy Bypass -File scripts\run_hub.ps1
# Opens the host page in the browser a few seconds after the hub starts.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root "hub\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "hub\.venv is missing. Run scripts\setup.bat first." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

$onPod = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -like "192.168.4.*" }
if (-not $onPod) {
    Write-Host "This laptop is not on the pod's Wi-Fi yet. Join 'Kinig-Pod' so the pod can find the hub." -ForegroundColor Yellow
}

$port = & $venvPython -c "from kinig_hub.config import load_config; print(load_config().hub['http_port'])"
Start-Job -ScriptBlock { param($p) Start-Sleep -Seconds 6; Start-Process "http://127.0.0.1:$p/host" } -ArgumentList $port | Out-Null

Write-Host "Starting the hub. Leave this window open. Ctrl+C stops it."
& $venvPython -m kinig_hub.main
