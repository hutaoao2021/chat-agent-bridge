$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $projectRoot '.venv/Scripts/pythonw.exe'
$startupScript = Join-Path $PSScriptRoot 'autostart.py'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'Python environment is missing' }
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
foreach ($entry in @(
    @{ Name = 'Chat Agent Bridge - Setup'; Action = 'configure' },
    @{ Name = 'Chat Agent Bridge - Start'; Action = 'run' },
    @{ Name = 'Chat Agent Bridge - Disable'; Action = 'disable' }
)) {
    $shortcutPath = Join-Path $desktop ($entry.Name + '.lnk')
    $shortcut = $shell.CreateShortcut($shortcutPath)
    if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.TargetPath -ne $pythonw) {
        throw "An unrelated shortcut already exists: $shortcutPath"
    }
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = '"' + $startupScript + '" ' + $entry.Action
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.WindowStyle = 7
    $shortcut.Description = 'Chat Agent Bridge background startup'
    $shortcut.Save()
}
Start-Process -FilePath $pythonw -ArgumentList ('"' + $startupScript + '" configure') -WorkingDirectory $projectRoot -WindowStyle Hidden
Write-Output 'Startup shortcuts created. Complete the local credential dialog once.'
