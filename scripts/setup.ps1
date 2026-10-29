# Kinig hub setup for Windows. Double-click scripts\setup.bat, or run:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# 1. Finds Python 3.11+ (python.org install, "Add python to PATH").
# 2. Creates hub\.venv and installs the hub into it.
# 3. Downloads the Whisper weights (needs internet, once).
# 4. Installs Ollama with winget if it is missing, then pulls the summary model.
# 5. Opens the firewall for the pod and phones (asks for admin once).
# Run it again any time. Finished steps are skipped or repeated safely.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host $text -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }

# ---------------------------------------------------------------- Python

Step "Finding Python 3.11 or newer"
$python = $null
foreach ($candidate in @(@("py", "-3"), @("python"))) {
    $exe = $candidate[0]
    $rest = @($candidate | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $ok = & $exe @rest -c "import sys; print(1 if sys.version_info >= (3, 11) else 0)" 2>$null
    } catch { continue }
    if ($ok -eq "1") { $python = $candidate; break }
}
if (-not $python) {
    Fail "Python 3.11+ was not found. Install it from https://www.python.org/downloads/ and tick 'Add python to PATH', then run this again."
}
$pyExe = $python[0]
$pyArgs = @($python | Select-Object -Skip 1)
& $pyExe @pyArgs --version

# ---------------------------------------------------------------- venv + hub

$venvPython = Join-Path $root "hub\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Step "Creating hub\.venv"
    & $pyExe @pyArgs -m venv hub\.venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtual environment." }
}

Step "Installing the hub (a few minutes the first time)"
& $venvPython -m pip install -q -U pip
& $venvPython -m pip install -e "hub[dev]"
if ($LASTEXITCODE -ne 0) { Fail "pip install failed. Check the internet connection and run this again." }

# ---------------------------------------------------------------- Ollama

Step "Checking Ollama"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Ollama is missing. Installing it with winget."
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    }
}
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $model = & $venvPython -c "from kinig_hub.config import load_config; print(load_config().models['summarizer']['options']['model'])"
    Write-Host "Pulling $model (skipped quickly if already there)"
    ollama pull $model
} else {
    Write-Host "Ollama is not installed. Get it from https://ollama.com/download, then run this again." -ForegroundColor Yellow
    Write-Host "Without it the hub still works and uses the simple rule-based summary." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- Whisper

Step "Downloading the Whisper weights"
& $venvPython scripts\download_models.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Model check reported a problem (see above). Whisper must be downloaded for transcription." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- firewall

Step "Opening the firewall for the pod and phones"
& (Join-Path $PSScriptRoot "windows_firewall.ps1")

Step "Setup finished"
Write-Host "Next: join this laptop to the Wi-Fi 'Kinig-Pod', then double-click scripts\run_hub.bat."
Read-Host "Press Enter to close"
