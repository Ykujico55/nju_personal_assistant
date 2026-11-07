param(
    [string]$AdminUrl = "http://127.0.0.1:8001",
    [string]$Profile = "",
    [ValidateRange(2, 30)][int]$Refresh = 5
)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot ".venv-win\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Missing .venv-win. Install the project with the tui extra first."
}
Push-Location -LiteralPath $repoRoot
try {
    $tuiArgs = @("-Xutf8", "-m", "personal_assistant.cli.main", "tui",
        "--admin-url", $AdminUrl, "--refresh", "$Refresh")
    if ($Profile) { $tuiArgs += @("--profile", $Profile) }
    & $pythonPath @tuiArgs
    $resultCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $resultCode
