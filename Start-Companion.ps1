param([switch]$AllowSleep)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.runtime/python/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { & (Join-Path $projectRoot 'Setup.ps1') }
$dataRoot = Join-Path $projectRoot 'data'
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$dashboardUrl = 'http://127.0.0.1:8787'
$healthy = $false
try { $healthy = (Invoke-RestMethod -Uri "$dashboardUrl/healthz" -TimeoutSec 2).ok } catch {}
if (-not $healthy) {
    $serviceScript = Join-Path $projectRoot 'run_service.py'
    $serviceArgs = @('"' + $serviceScript + '"')
    if ($AllowSleep) { $serviceArgs += '--allow-sleep' }
    $process = Start-Process -FilePath $pythonExe -ArgumentList $serviceArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dataRoot 'server.log') -RedirectStandardError (Join-Path $dataRoot 'server-error.log')
    $process.Id | Set-Content -LiteralPath (Join-Path $dataRoot 'server.pid')
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try { $healthy = (Invoke-RestMethod -Uri "$dashboardUrl/healthz" -TimeoutSec 1).ok; if ($healthy) { break } } catch {}
        Start-Sleep -Milliseconds 500
    }
    if (-not $healthy) { throw 'Service could not start. Read data/server-error.log.' }
}
$ownerStatus = Invoke-RestMethod -Uri "$dashboardUrl/api/auth/status" -TimeoutSec 3
if ($ownerStatus.configured) {
    try { & (Join-Path $projectRoot 'Start-Desktop.ps1') }
    catch { Write-Warning 'Desktop startup needs attention. Use Connect Railway in the dashboard to retry.' }
}
$chromePath = Join-Path ${env:ProgramFiles} 'Google/Chrome/Application/chrome.exe'
$browserPreference = Join-Path $dataRoot 'desktop/browser.json'
$dashboardProfileName = 'dashboard-profile'
if (Test-Path -LiteralPath $browserPreference) {
    $preferredBrowser = Get-Content -LiteralPath $browserPreference -Raw | ConvertFrom-Json
    if (Test-Path -LiteralPath $preferredBrowser.binary) {
        $chromePath = $preferredBrowser.binary
        if ($preferredBrowser.name -eq 'Brave') { $dashboardProfileName = 'dashboard-profile-brave' }
    }
}
if (Test-Path -LiteralPath $chromePath) {
    $dashboardProfile = Join-Path $dataRoot $dashboardProfileName
    Start-Process -FilePath $chromePath -ArgumentList @("--app=$dashboardUrl", ('--user-data-dir="' + $dashboardProfile + '"'), '--no-first-run')
} else { Start-Process $dashboardUrl }
Write-Host 'Railway Companion is running. Closing its window does not stop monitoring.'
Write-Host 'Use Stop-Companion.ps1 to stop the service. Sign in and configure your watchlists.'
