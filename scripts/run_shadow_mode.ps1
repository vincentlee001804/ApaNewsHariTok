# FYP2 Stage 1b — run the bot with utility-alert shadow logging captured.
#
# Shadow mode is ON by default (UTILITY_ALERT_SHADOW_MODE=true in .env), so this
# only *detects and logs* alerts — nothing is pushed to users.
#
# Usage from the repo root (venv active):
#   powershell -ExecutionPolicy Bypass -File scripts/run_shadow_mode.ps1
#
# Git Bash alternative:
#   python -m src.bot.bot_main 2>&1 | tee -a logs/utility_shadow_$(date +%Y%m%d).log

$ErrorActionPreference = "Stop"

$logDir = Join-Path $PSScriptRoot "..\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("utility_shadow_{0}.log" -f (Get-Date -Format "yyyyMMdd"))

Write-Host "Utility-alert shadow log -> $logFile"
Write-Host "Shadow mode: alerts are stored + counted, ZERO pushes to users."

python -m src.bot.bot_main *>&1 | Tee-Object -FilePath $logFile -Append
