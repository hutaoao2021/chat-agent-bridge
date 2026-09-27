param(
    [Parameter(Mandatory)][string]$PythonRoot,
    [Parameter(Mandatory)][string]$TunnelClient,
    [Parameter(Mandatory)][string]$Iscc,
    [string]$OutputDir = ('dist/release-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
)
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path (Resolve-Path -LiteralPath $PythonRoot) 'python.exe'
$tunnelRoot = Split-Path -Parent (Resolve-Path -LiteralPath $TunnelClient)
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $sourceRoot $OutputDir))
& $pythonExe -B (Join-Path $PSScriptRoot 'build_bundle.py') --source $sourceRoot --python-root $PythonRoot --tunnel-root $tunnelRoot --output $releaseRoot
if ($LASTEXITCODE -ne 0) { throw 'Application bundle failed' }
& $Iscc /Qp ("/DBundleDir=" + (Join-Path $releaseRoot 'bundle')) ("/DOutputDir=" + $releaseRoot) (Join-Path $sourceRoot 'packaging/windows/installer.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
$hashLines = Get-ChildItem -LiteralPath $releaseRoot -File | Where-Object Name -ne 'SHA256SUMS.txt' | ForEach-Object {
    (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() + '  ' + $_.Name
}
$hashLines | Set-Content -LiteralPath (Join-Path $releaseRoot 'SHA256SUMS.txt') -Encoding ascii
Write-Output "Release ready: $releaseRoot"
