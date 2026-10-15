#requires -Version 7.3
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ -not [string]::IsNullOrWhiteSpace($_) })]
    [string]$Name,
    [string]$Client = "",
    [string]$Request = "",
    [string]$WorkRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) "work"),
    [string]$TaskKnowledgeRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) "任务知识库"),
    [ValidateRange(1, 5)]
    [int]$KnowledgeLimit = 3,
    [ValidateRange(1, 300)]
    [int]$LockTimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$PSNativeCommandArgumentPassing = 'Standard'
$repoRoot = [System.IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$root = [System.IO.Path]::GetFullPath($WorkRoot)
[System.IO.Directory]::CreateDirectory($root) | Out-Null

$lockPath = Join-Path $root ".new-work.lock"
$lockStream = $null
$deadline = [DateTime]::UtcNow.AddSeconds($LockTimeoutSeconds)
while ($null -eq $lockStream) {
    try {
        $lockStream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    }
    catch [System.IO.IOException] {
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "Timed out waiting for the Work allocation lock: $lockPath"
        }
        Start-Sleep -Milliseconds 100
    }
}

$stagingRoot = $null
try {
$numbers = @(
    Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d+$' } |
        ForEach-Object { [int]$_.Name }
)
$cancelledRoot = Join-Path $root "_cancelled"
if (Test-Path -LiteralPath $cancelledRoot -PathType Container) {
    $numbers += @(Get-ChildItem -LiteralPath $cancelledRoot -Directory | ForEach-Object {
        if ($_.Name -match '^(\d+)(?:-|$)') { [int]$Matches[1] }
    })
}
$sequencePath = Join-Path $root ".work-sequence.json"
if (Test-Path -LiteralPath $sequencePath -PathType Leaf) {
    $sequence = Get-Content -LiteralPath $sequencePath -Raw | ConvertFrom-Json
    if (-not $sequence.PSObject.Properties["high_water_mark"] -or [int]$sequence.high_water_mark -lt 0) {
        throw "Invalid Work sequence record: $sequencePath"
    }
    $numbers += [int]$sequence.high_water_mark
}
$next = if ($numbers.Count) { ($numbers | Measure-Object -Maximum).Maximum + 1 } else { 1 }
while (Test-Path -LiteralPath (Join-Path $root ([string]$next))) {
    $next += 1
}

$finalTaskRoot = Join-Path $root ([string]$next)
# Only a complete package receives a numeric name. A failure cannot leave a
# half-created Work that later tooling mistakes for a valid task.
$stagingRoot = Join-Path $root (".new-work-" + [guid]::NewGuid().ToString("N"))
$taskRoot = $stagingRoot
foreach ($relative in @(
    "input/source",
    "input/reference",
    "workspace",
    "output"
)) {
    [System.IO.Directory]::CreateDirectory((Join-Path $taskRoot $relative)) | Out-Null
}

$createdAt = (Get-Date).ToString("o")
$record = [ordered]@{
    schema_version = 1
    closure_schema_version = 3
    storage_profile = "minimal"
    conversation_retention = "disabled"
    work_id = [string]$next
    work_uuid = [guid]::NewGuid().ToString()
    name = $Name
    client = $Client
    status = "intake"
    user_approved = $false
    knowledge_status = "not_started"
    created_at = $createdAt
    core_project = [IO.Path]::GetRelativePath($finalTaskRoot, (Join-Path $repoRoot "aippt-studio")).Replace('\', '/')
    studio_jobs_dir = "workspace/studio-jobs"
    output_dir = "output"
}
$record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $taskRoot "work.json") -Encoding utf8


$requestText = if ($Request.Trim()) { $Request.Trim() } else { "待补充。" }
@"
# 用户要求

$requestText
"@ | Set-Content -LiteralPath (Join-Path $taskRoot "input/request.md") -Encoding utf8


$wrapper = @'
#requires -Version 7.3
$AipptArgs = @($args)
$ErrorActionPreference = "Stop"
$repositoryRelative = (Get-Content -LiteralPath (Join-Path $PSScriptRoot ".studio-root") -Raw).TrimEnd("`r", "`n")
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot $repositoryRelative)).Path
$previousJobsDir = $env:AIPPT_JOBS_DIR
$previousWorkDir = $env:AIPPT_WORK_DIR
try {
    $env:AIPPT_JOBS_DIR = Join-Path $PSScriptRoot "studio-jobs"
    $env:AIPPT_WORK_DIR = [IO.Directory]::GetParent($PSScriptRoot).FullName
    & (Join-Path $repoRoot "scripts/run-studio.ps1") @AipptArgs
    $studioExitCode = $LASTEXITCODE
}
finally {
    $env:AIPPT_JOBS_DIR = $previousJobsDir
    $env:AIPPT_WORK_DIR = $previousWorkDir
}
exit $studioExitCode
'@
$wrapper | Set-Content -LiteralPath (Join-Path $taskRoot "workspace/aippt.ps1") -Encoding utf8
$repositoryRelative = [IO.Path]::GetRelativePath((Join-Path $finalTaskRoot "workspace"), $repoRoot).Replace('\', '/')
$repositoryRelative | Set-Content -LiteralPath (Join-Path $taskRoot "workspace/.studio-root") -Encoding utf8NoBOM
$shellWrapper = @'
#!/bin/sh
set -eu
workspace_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IFS= read -r repository_relative < "$workspace_dir/.studio-root"
repo_root=$(CDPATH= cd -- "$workspace_dir/$repository_relative" && pwd)
exec "$repo_root/scripts/pwsh.sh" -NoLogo -NoProfile -NonInteractive -File "$workspace_dir/aippt.ps1" "$@"
'@
$shellWrapperPath = Join-Path $taskRoot "workspace/aippt.sh"
$shellWrapper | Set-Content -LiteralPath $shellWrapperPath -Encoding utf8NoBOM
if (-not $IsWindows) {
    & chmod u+x $shellWrapperPath
    if ($LASTEXITCODE -ne 0) { throw "Cannot make the Work shell wrapper executable: $shellWrapperPath" }
}

. (Join-Path $PSScriptRoot "studio-runtime.ps1")
$studioExecutable = $null
try { $studioExecutable = Get-StudioPython $repoRoot } catch { $runtimeError = $_.Exception.Message }
$record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $taskRoot "work.json") -Encoding utf8

# The rename happens on the same filesystem while the allocation lock is held.
$sequenceTmp = "$sequencePath.tmp"
[ordered]@{ schema_version = 1; high_water_mark = [int]$next } | ConvertTo-Json | Set-Content -LiteralPath $sequenceTmp -Encoding utf8
[IO.File]::Move($sequenceTmp, $sequencePath, $true)
[IO.Directory]::Move($stagingRoot, $finalTaskRoot)
$stagingRoot = $null
$lockStream.Dispose()
$lockStream = $null
$taskRoot = $finalTaskRoot
# Minimal Work allocation never binds or snapshots the task conversation.
if ($root.TrimEnd([IO.Path]::DirectorySeparatorChar) -eq (Join-Path $repoRoot "work") -and $studioExecutable) {
    $initializeArgs = @('-X', 'utf8', '-m', 'aippt_studio.maintenance', 'initialize', '--repo-root', $repoRoot, '--work-dir', $taskRoot)
    if ($env:CODEX_THREAD_ID) { $initializeArgs += @('--thread-id', $env:CODEX_THREAD_ID) }
    $startup = [ordered]@{ attempted_at = (Get-Date -Format o); status = 'unavailable'; exit_code = $null; detail = '' }
    try {
        $startup.detail = (@(& $studioExecutable @initializeArgs 2>&1) -join "`n")
        $startup.exit_code = $LASTEXITCODE
        if ($LASTEXITCODE -eq 0) { $startup.status = 'initialized' }
    }
    catch { $startup.detail = $_.Exception.Message }
    if ($startup.status -ne 'initialized') { [Console]::Error.WriteLine("Minimal Work runtime initialization is unavailable: $($startup.detail)") }
}

[pscustomobject]@{
    work_id = [string]$next
    root = $taskRoot
    request = (Join-Path $taskRoot "input/request.md")
    workspace = (Join-Path $taskRoot "workspace")
    output = (Join-Path $taskRoot "output")
    storage_profile = "minimal"
    authoring_context = $(if (Test-Path -LiteralPath (Join-Path $taskRoot "workspace/authoring-context.json") -PathType Leaf) { Join-Path $taskRoot "workspace/authoring-context.json" } else { $null })
} | ConvertTo-Json -Depth 3
}
finally {
    try {
        if ($stagingRoot -and (Test-Path -LiteralPath $stagingRoot -PathType Container)) {
            Remove-Item -LiteralPath $stagingRoot -Recurse -Force
        }
    }
    finally { if ($lockStream) { $lockStream.Dispose() } }
}
