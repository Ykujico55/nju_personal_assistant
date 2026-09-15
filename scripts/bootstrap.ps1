param(
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $BundledPython) {
        $PythonExecutable = $BundledPython
    } else {
        $PythonExecutable = (Get-Command python -ErrorAction Stop).Source
    }
}

Push-Location $ProjectRoot
try {
    & $PythonExecutable -m venv .venv-win
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & ".venv-win\Scripts\python.exe" -m pip install -r dependency.lock
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & ".venv-win\Scripts\python.exe" -m pip install --no-build-isolation --no-deps -e .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & ".venv-win\Scripts\assistantctl.exe" doctor
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
