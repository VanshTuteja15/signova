<#
.SYNOPSIS
  Start SIGNOVA in simulation mode (no hardware needed) and open the dashboard.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\run_sim_demo.ps1
#>
param([int]$HttpPort = 8000, [switch]$NoBrowser)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "run.ps1") -Mode sim -HttpPort $HttpPort -NoBrowser:$NoBrowser
