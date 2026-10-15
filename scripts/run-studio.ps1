#requires -Version 7.3
# Plain script arguments deliberately avoid PowerShell's common-parameter binding.
# For example, --verbose and an empty string must reach the Python CLI unchanged.
$AipptArgs = @($args)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot "studio-runtime.ps1")
$python = Get-StudioPython $repoRoot
$previousPythonUtf8 = $env:PYTHONUTF8
$previousPythonIoEncoding = $env:PYTHONIOENCODING
$previousRuntimeNodeModules = $env:RUNTIME_NODE_MODULES
$previousAipptNode = $env:AIPPT_NODE
$PSNativeCommandArgumentPassing = 'Standard'
try {
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $runtimeEnvironment = Get-StudioRuntimeEnvironment $python
    if ($env:AIPPT_JOBS_DIR -and (Test-Path -LiteralPath (Join-Path (Split-Path $env:AIPPT_JOBS_DIR -Parent) "runtime-environment.json") -PathType Leaf)) {
        $env:AIPPT_NODE = $null
        $env:RUNTIME_NODE_MODULES = $null
    }
    if ($runtimeEnvironment.ContainsKey("AIPPT_NODE")) { $env:AIPPT_NODE = $runtimeEnvironment["AIPPT_NODE"] }
    if ($runtimeEnvironment.ContainsKey("RUNTIME_NODE_MODULES")) { $env:RUNTIME_NODE_MODULES = $runtimeEnvironment["RUNTIME_NODE_MODULES"] }
    & $python -m aippt_studio @AipptArgs
    $studioExitCode = $LASTEXITCODE
}
finally {
    $env:PYTHONUTF8 = $previousPythonUtf8
    $env:PYTHONIOENCODING = $previousPythonIoEncoding
    $env:RUNTIME_NODE_MODULES = $previousRuntimeNodeModules
    $env:AIPPT_NODE = $previousAipptNode
}
exit $studioExitCode
