param([int]$Port = 8900)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $projectRoot '.venv/Scripts/python.exe') -c "import chat_agent_bridge; print('Bridge', chat_agent_bridge.__version__)"
Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 5
