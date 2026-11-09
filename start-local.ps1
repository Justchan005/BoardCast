param([switch]$Offline, [int]$Port = 8081, [switch]$SampleItems)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv and install backend/requirements.txt and agent/requirements.txt first (see README).'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    $launchArgs = @('-B', '-m', 'scripts.run_local', '--port', "$Port")
    if ($Offline) { $launchArgs += '--offline' }
    if ($SampleItems) { $launchArgs += '--sample-items' }
    & $pythonPath @launchArgs
} finally { Pop-Location }
