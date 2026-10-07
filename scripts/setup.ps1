<#
.SYNOPSIS
  One-time setup for SIGNOVA on Windows: virtual environment, dependencies, checks and tests.

.DESCRIPTION
  Everything is installed inside this folder (.venv, models\, .platformio\). Nothing is installed
  globally and no system settings are changed.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
      Create .venv, install dependencies, run ruff and the test suite.
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Models
      Also download the speech models (~280 MB) so the speech tests run with real models.
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Firmware
      Also run the firmware unit tests and build the ESP32 firmware (first build downloads ~1 GB of toolchain).
#>
param(
    [switch]$Models,
    [switch]$Firmware,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Check([string]$What) {
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE). See the messages above." }
}

# ------------------------------------------------------------------ Python
Step "Looking for Python 3.11 or newer"
function Invoke-Python([string]$Launcher, [string[]]$PyArgs) {
    # $Launcher is e.g. "py -3" or "python"
    $parts = $Launcher.Split(" ")
    $exe = $parts[0]
    $pre = @()
    if ($parts.Count -gt 1) { $pre = $parts[1..($parts.Count - 1)] }
    & $exe @pre @PyArgs
}

$PyLauncher = $null
foreach ($candidate in @("py -3", "python", "python3")) {
    try {
        $v = Invoke-Python $candidate @("-c", "import sys; print('%d.%d' % sys.version_info[:2])") 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            $ver = "$v".Trim().Split(".")
            if ([int]$ver[0] -eq 3 -and [int]$ver[1] -ge 11) {
                $PyLauncher = $candidate
                Write-Host "Found Python $("$v".Trim()) ($candidate)"
                break
            }
        }
    } catch { }
}
if (-not $PyLauncher) {
    throw "Python 3.11+ not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run this script again."
}

# ------------------------------------------------------------------ venv + dependencies
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Step "Creating the virtual environment in .venv"
    Invoke-Python $PyLauncher @("-m", "venv", ".venv")
    Check "Creating .venv"
}

Step "Installing dependencies (first run takes a few minutes)"
& $VenvPy -m pip install --upgrade pip --quiet
Check "Upgrading pip"
& $VenvPy -m pip install -r requirements.txt --quiet
Check "Installing requirements.txt"
& $VenvPy -m pip install -e . --no-deps --quiet
Check "Installing the signova package"

if (-not (Test-Path (Join-Path $Root ".env"))) {
    Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
    Write-Host "Created .env from .env.example (add ANTHROPIC_API_KEY there to use the Claude gloss engine)."
}

# ------------------------------------------------------------------ browser for the dashboard test
$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($chrome) {
    Write-Host "Google Chrome found: the dashboard test uses it."
} else {
    Step "Google Chrome not found: installing Playwright's Chromium into .playwright-browsers"
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Root ".playwright-browsers"
    & $VenvPy -m playwright install chromium
    Check "Installing Chromium for Playwright"
}

# ------------------------------------------------------------------ checks
Step "Checking the hand config and sign library"
& (Join-Path $Root ".venv\Scripts\signova.exe") check
Check "signova check"

if ($Models) {
    Step "Downloading speech models into models\ (Vosk ~130 MB, Whisper base.en ~145 MB)"
    & (Join-Path $Root ".venv\Scripts\signova.exe") download-models
    Check "Downloading models"
}

if (-not $SkipTests) {
    Step "Lint (ruff)"
    & $VenvPy -m ruff check .
    Check "ruff"
    Step "Tests (pytest). Speech tests that need models are skipped until you run with -Models."
    & $VenvPy -m pytest -q
    Check "pytest"
}

if ($Firmware) {
    $env:PLATFORMIO_CORE_DIR = Join-Path $Root ".platformio"
    $Pio = Join-Path $Root ".venv\Scripts\pio.exe"
    Push-Location (Join-Path $Root "firmware\signova_hand")
    try {
        Step "Firmware unit tests (pio test -e native, needs a g++ on PATH)"
        & $Pio test -e native
        Check "Firmware native tests"
        Step "Building the ESP32 firmware (pio run -e esp32dev)"
        & $Pio run -e esp32dev
        Check "ESP32 build"
    } finally { Pop-Location }
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "Start the simulation demo with:  powershell -ExecutionPolicy Bypass -File scripts\run_sim_demo.ps1"
