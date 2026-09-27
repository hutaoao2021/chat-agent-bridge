param([string]$Config = 'config.local.toml', [string]$DataDir = '.data')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create .venv and install dependencies first; see docs/setup.md' }
& $pythonPath -m chat_agent_bridge.main --config $Config --data-dir $DataDir
