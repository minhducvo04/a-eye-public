$ErrorActionPreference = "Stop"
# Configure local model paths and the venv in the environment before launching.
foreach ($name in @('PC_TAILSCALE_IP', 'STT_MODEL', 'YOLO_WEIGHTS', 'KOKORO_DIR', 'BOX_VENV')) {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "Set $name before running run_box.ps1" }
}
$env:HF_HUB_OFFLINE = "1"
. (Join-Path $env:BOX_VENV 'Scripts/Activate.ps1')
Set-Location (Split-Path $PSScriptRoot -Parent)
# Bind only to the configured tailnet interface.
python -m uvicorn boxsvc.app:app --host $env:PC_TAILSCALE_IP --port 8090
