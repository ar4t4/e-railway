$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
foreach ($entry in @(@('data/server.pid','run_service.py'), @('data/desktop/agent.pid','run_desktop.py'))) {
    $pidFile = Join-Path $projectRoot $entry[0]
    if (Test-Path -LiteralPath $pidFile) {
        $targetPid = 0
        if ([int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$targetPid)) {
            $targetProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid" -ErrorAction SilentlyContinue
            $expectedScript = Join-Path $projectRoot $entry[1]
            if ($targetProcess -and $targetProcess.CommandLine.Contains($expectedScript)) {
                Stop-Process -Id $targetPid
            }
        }
    }
}
Write-Host 'Companion service and agent stopped. The railway browser remains available for any pending checkout.'
