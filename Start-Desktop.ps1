param([switch]$Pair, [ValidateSet('Chrome','Brave')][string]$Browser)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.runtime/python/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { & (Join-Path $projectRoot 'Setup.ps1') }
$agentRoot = Join-Path $projectRoot 'data/desktop'
New-Item -ItemType Directory -Force -Path $agentRoot | Out-Null
$agentScript = Join-Path $projectRoot 'run_desktop.py'
$pidPath = Join-Path $agentRoot 'agent.pid'
if (Test-Path -LiteralPath $pidPath) {
    $priorPid = 0
    if ([int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$priorPid)) {
        $priorProcess = $null
        try { $priorProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$priorPid" -ErrorAction Stop } catch {}
        if ($priorProcess -and $priorProcess.CommandLine.Contains($agentScript)) {
            if ($Pair -or $Browser) { throw 'Quit the running desktop agent from its tray menu before changing pairing or browser.' }
            Write-Host 'The desktop agent is already running.'
            return
        }
    }
}
if ($Browser) {
    $relativeBinary = if ($Browser -eq 'Brave') { 'BraveSoftware/Brave-Browser/Application/brave.exe' } else { 'Google/Chrome/Application/chrome.exe' }
    $browserBinary = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA) | Where-Object { $_ } | ForEach-Object { Join-Path $_ $relativeBinary } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $browserBinary) { throw "$Browser is not installed in a standard location." }
    @{ name=$Browser; binary=$browserBinary } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $agentRoot 'browser.json') -Encoding UTF8
}
if ($Pair) {
    & $pythonExe $agentScript --pair --pair-only
    if ($LASTEXITCODE -ne 0) { throw 'Desktop pairing failed.' }
}
$agentArgs = @('"' + $agentScript + '"')
if (-not $Pair) { $agentArgs += '--local' }
$process = Start-Process -FilePath $pythonExe -ArgumentList $agentArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $agentRoot 'agent.log') -RedirectStandardError (Join-Path $agentRoot 'agent-error.log')
Start-Sleep -Seconds 2
if ($process.HasExited -and $process.ExitCode -ne 0) { throw 'Desktop agent could not start. Read data/desktop/agent-error.log.' }
Write-Host 'Desktop agent is available. Click Connect Railway in the app; sign-in syncs automatically.'
