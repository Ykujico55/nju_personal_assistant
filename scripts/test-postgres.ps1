# Runs the real PostgreSQL F01 + F02 + F04 + F05 acceptance suites.
#
# Requires a reachable PostgreSQL 17 + pgvector server (see compose.yaml). The
# target database name must end in `_test`; tests create and drop their own
# uniquely named throwaway databases and never touch the base database.
#
#   docker compose up -d postgres
#   ./scripts/test-postgres.ps1
#
# Override with: -DatabaseUrl "postgresql://user:pass@host:port/name_test"

param(
    [string]$DatabaseUrl = "postgresql://assistant:change-me@127.0.0.1:5432/assistant_test"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PythonExecutable = Join-Path $ProjectRoot ".venv-win\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonExecutable)) {
    throw "Missing .venv-win. Run scripts/bootstrap.ps1 first."
}

$env:PA_TEST_DATABASE_URL = $DatabaseUrl

Push-Location $ProjectRoot
try {
    & $PythonExecutable -m pytest tests/integration/test_postgres_f01.py tests/integration/test_postgres_f02.py tests/integration/test_postgres_f04.py tests/integration/test_postgres_f05.py tests/integration/test_personal_knowledge_worker_real.py -v
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
