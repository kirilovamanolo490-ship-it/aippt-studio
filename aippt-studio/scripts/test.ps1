param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path (Split-Path $projectRoot -Parent) "scripts/studio-runtime.ps1")
$python = Get-StudioPython (Split-Path $projectRoot -Parent)

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) "aippt-studio-pytest-$PID"
$exitCode = 1
try {
    & $python -B -m pytest -p no:cacheprovider "--basetemp=$tempRoot" (Join-Path $projectRoot "tests") @PytestArgs
    $exitCode = $LASTEXITCODE
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

exit $exitCode
