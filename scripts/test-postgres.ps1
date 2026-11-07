# Runs the real PostgreSQL F01 + F02 + F04 + F05 + F06 + F07 + F08.3/F08.5 suites.
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
    [string]$DatabaseUrl = "postgresql://assistant:change-me@127.0.0.1:5432/assistant_test",
    [switch]$CollectOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PythonExecutable = Join-Path $ProjectRoot ".venv-win\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonExecutable)) {
    throw "Missing .venv-win. Run scripts/bootstrap.ps1 first."
}

$PreviousDatabaseUrl = $env:PA_TEST_DATABASE_URL
$SavedEnvironment = @{}
$LocationPushed = $false
try {
    Get-ChildItem Env: | Where-Object {
        ($_.Name -like "PA_*" -and $_.Name -ne "PA_TEST_DATABASE_URL") -or
        $_.Name -eq "PYTHONPATH"
    } | ForEach-Object {
        $SavedEnvironment[$_.Name] = $_.Value
        [Environment]::SetEnvironmentVariable($_.Name, $null, "Process")
    }
    $env:PA_TEST_DATABASE_URL = $DatabaseUrl
    Push-Location $ProjectRoot
    $LocationPushed = $true
    $Tests = @(
        "tests/integration/test_postgres_f01.py",
        "tests/integration/test_postgres_f02.py",
        "tests/integration/test_postgres_f04.py",
        "tests/integration/test_postgres_f05.py",
        "tests/integration/test_personal_knowledge_worker_real.py",
        "tests/integration/test_postgres_f06.py",
        "tests/integration/test_smail_worker_real.py",
        "tests/integration/test_postgres_f07.py",
        "tests/integration/test_ehall_worker_real.py",
        "tests/integration/test_pwa_form_catalog_worker_f08.py",
        "tests/integration/test_postgres_delivery.py",
        "tests/integration/test_runtime_remediation_postgres.py",
        "tests/integration/test_recovery_remediation_delivery.py",
        "tests/integration/test_extension_recovery_concurrency_postgres.py",
        "tests/integration/test_knowledge_meta_postgres.py",
        "tests/integration/test_runtime_conversation_postgres.py",
        "tests/integration/test_task_queue_management_postgres.py",
        "tests/integration/test_task_reconciliation_postgres.py"
    )
    $Options = @("-v", "-p", "no:cacheprovider")
    if ($CollectOnly) { $Options += "--collect-only" }
    & $PythonExecutable -Xutf8 -B -m pytest @Tests @Options
    exit $LASTEXITCODE
} finally {
    if ($LocationPushed) { Pop-Location }
    $env:PA_TEST_DATABASE_URL = $PreviousDatabaseUrl
    foreach ($Name in $SavedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($Name, $SavedEnvironment[$Name], "Process")
    }
}
