param(
    [ValidateSet("start", "stop", "status", "freeze", "resume", "install-startup", "uninstall-startup")]
    [string]$Action = "status",
    [string]$Profile = "var/config/host.json"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv-win/Scripts/python.exe"
$profilePath = [System.IO.Path]::GetFullPath((Join-Path $repo $Profile))
$releasePointer = Join-Path $repo "var/releases/current.json"
if (Test-Path -LiteralPath $releasePointer) {
    $candidate = Get-Content -LiteralPath $releasePointer -Raw | ConvertFrom-Json
    $releaseRoot = [System.IO.Path]::GetFullPath((Join-Path $repo "var/releases")) + [System.IO.Path]::DirectorySeparatorChar
    $candidatePython = [System.IO.Path]::GetFullPath($candidate.python)
    if (-not $candidatePython.StartsWith($releaseRoot, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $candidatePython -PathType Leaf)) { throw "Unsafe release pointer" }
    $python = $candidatePython
}
Set-Location -LiteralPath $repo
if ($Action -eq "start") {
    # The guardian owns its children; stop communicates through its control file.
    Start-Process -FilePath $python -ArgumentList @("-Xutf8", "-m", "personal_assistant.host_entry", "serve", "--profile", ('"' + $profilePath + '"')) -WorkingDirectory $repo -WindowStyle Hidden
} elseif ($Action -in @("install-startup", "uninstall-startup")) {
    & $python -Xutf8 scripts/startup-task.py $Action --profile $profilePath
    if ($LASTEXITCODE -ne 0) { throw "Startup registration failed" }
} else {
    & $python -Xutf8 -m personal_assistant.host_entry $Action --profile $profilePath
    if ($LASTEXITCODE -ne 0) { throw "Host operation failed" }
}
