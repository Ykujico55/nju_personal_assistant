$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PythonExecutable = Join-Path $ProjectRoot ".venv-win\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonExecutable)) {
    throw "Missing .venv-win. Run scripts/bootstrap.ps1 first."
}

$ProcessExitCode = 1
Push-Location $ProjectRoot
try {
    & $PythonExecutable -m uvicorn personal_assistant.admin_app:app --env-file .env --host 127.0.0.1 --port 8001
    $ProcessExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $ProcessExitCode
