<#
.SYNOPSIS
  Start the SIGNOVA server and open the dashboard.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\run.ps1                  # mode from config\hand.yaml (sim by default)
  powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Mode serial -Port COM5
  powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Mode emulator
#>
param(
    [ValidateSet("", "sim", "emulator", "serial")][string]$Mode = "",
    [string]$Port = "",
    [int]$HttpPort = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Signova = Join-Path $Root ".venv\Scripts\signova.exe"
if (-not (Test-Path $Signova)) {
    throw "SIGNOVA is not installed yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1"
}

$cliArgs = @("serve", "--port", "$HttpPort")
switch ($Mode) {
    "sim" { $cliArgs += "--sim" }
    "emulator" { $cliArgs += "--emulator" }
    "serial" { $cliArgs += "--serial"; if ($Port) { $cliArgs += $Port } }
}
if (-not $NoBrowser) { $cliArgs += "--open" }

Write-Host "Starting SIGNOVA on http://127.0.0.1:$HttpPort  (press Ctrl+C to stop)" -ForegroundColor Cyan
& $Signova @cliArgs
