param(
    [ValidateSet("core", "full", "integration", "plugins")]
    [string]$Suite = "core",
    [string[]]$TestPath = @(),
    [switch]$CollectOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PythonExecutable = Join-Path $ProjectRoot ".venv-win\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExecutable)) {
    throw "Missing .venv-win. Run scripts/bootstrap.ps1 first."
}

# Test children must not inherit the running assistant's profile/credentials.
# The explicitly configured disposable database is the only PA_* exception.
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

    Push-Location $ProjectRoot
    $LocationPushed = $true
    $PytestOptions = @("-o", "addopts=", "-q", "--strict-markers", "-p", "no:cacheprovider", "--durations=5")
    if ($CollectOnly) { $PytestOptions += "--collect-only" }
    $Selections = @()
    if ($TestPath.Count -gt 0) {
        $Selections += ,$TestPath
    } else {
        switch ($Suite) {
            "core" {
                $Paths = @(Get-Content -LiteralPath (Join-Path $ProjectRoot "tests/core-suite.txt") |
                    ForEach-Object { $_.Trim() } |
                    Where-Object { $_ -and -not $_.StartsWith("#") })
                if ($Paths.Count -eq 0) { throw "The core test selection is empty." }
                $Selections += ,$Paths
            }
            "full" { $Selections += ,@("tests") }
            "integration" { $Selections += ,@("tests/integration") }
        }
        # Separate pytest processes preserve each independent plugin's imports/config.
        if ($Suite -in @("full", "plugins")) {
            foreach ($Plugin in @("career_apply", "plugin_developer", "personal_files")) {
                $Selections += ,@("-c", "extensions/$Plugin/pyproject.toml", "extensions/$Plugin/tests")
            }
        }
    }
    foreach ($Selection in $Selections) {
        Write-Host "Test selection: $($Selection -join ' ')"
        & $PythonExecutable -Xutf8 -B -m pytest @PytestOptions @Selection
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    if (-not $CollectOnly) {
        & $PythonExecutable -m ruff check src extension_sdk extensions tests
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $PythonExecutable -m mypy src\personal_assistant extension_sdk\src\personal_assistant_sdk extensions\example_echo\src\example_echo extensions\personal_knowledge\src\personal_knowledge extensions\nju_smail\src\nju_smail extensions\nju_ehall\src\nju_ehall extensions\assistant_workflows\src\assistant_workflows
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
} finally {
    if ($LocationPushed) { Pop-Location }
    foreach ($Name in $SavedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($Name, $SavedEnvironment[$Name], "Process")
    }
}
