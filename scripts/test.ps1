$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PythonExecutable = Join-Path $ProjectRoot ".venv-win\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonExecutable)) {
    throw "Missing .venv-win. Run scripts/bootstrap.ps1 first."
}

Push-Location $ProjectRoot
try {
    & $PythonExecutable -m pytest
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $PythonExecutable -m ruff check src extension_sdk extensions tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $PythonExecutable -m mypy src\personal_assistant extension_sdk\src\personal_assistant_sdk extensions\example_echo\src\example_echo
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
