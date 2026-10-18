param([switch]$SkipDesktop)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot '.runtime/python'
$pythonExe = Join-Path $runtimeRoot 'python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
    $archivePath = Join-Path $projectRoot '.runtime/python.zip'
    Write-Host 'Downloading the portable Python runtime from python.org...'
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $archivePath
    Expand-Archive -LiteralPath $archivePath -DestinationPath $runtimeRoot -Force
    $pathConfig = Join-Path $runtimeRoot 'python312._pth'
    @('python312.zip', '.', '..\..', 'import site') | Set-Content -LiteralPath $pathConfig
}
& $pythonExe -m pip --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    $pipScript = Join-Path $runtimeRoot 'get-pip.py'
    Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $pipScript
    & $pythonExe $pipScript --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw 'Python package setup failed.' }
}
$requirementsPath = Join-Path $projectRoot $(if ($SkipDesktop) {'requirements.txt'} else {'requirements-desktop.txt'})
& $pythonExe -m pip install --requirement $requirementsPath --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Ready. Run Start-Companion.ps1 to open the dashboard.'
