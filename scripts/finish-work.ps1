[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$WorkId,
    [Parameter(Mandatory = $true)]
    [switch]$UserApproved,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ApprovalNote,
    [Parameter(Mandatory = $true)]
    [ValidateSet("promoted", "no-new-knowledge")]
    [string]$KnowledgeStatus,
    [string[]]$KnowledgeFiles = @(),
    [string]$TaskKnowledgeRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) "任务知识库"),
    [string]$WorkRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) "work")
)

$ErrorActionPreference = "Stop"
if (-not $UserApproved) {
    throw "Work cannot be completed without explicit user approval"
}

$repoRoot = [System.IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$root = [System.IO.Path]::GetFullPath($WorkRoot)
$taskRoot = Join-Path $root ([string]$WorkId)
$recordPath = Join-Path $taskRoot "work.json"
if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
    throw "Work record is missing: $recordPath"
}
. (Join-Path $PSScriptRoot "work-lifecycle-lock.ps1")
$lifecycleStream = Open-WorkLifecycleRecord -RecordPath $recordPath
try {
$record = (Read-WorkLifecycleRecord -Stream $lifecycleStream) | ConvertFrom-Json
if ($record.status -ne "review") {
    throw "Work $WorkId must be in review before it can be completed; current status: $($record.status)"
}

function Get-SafeRelativePath([string]$BasePath, [string]$TargetPath) {
    $separator = [IO.Path]::DirectorySeparatorChar
    $base = [IO.Path]::GetFullPath($BasePath).TrimEnd([char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar))
    $target = [IO.Path]::GetFullPath($TargetPath)
    $comparison = if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) { [StringComparison]::OrdinalIgnoreCase } else { [StringComparison]::Ordinal }
    if (-not $target.StartsWith("$base$separator", $comparison)) {
        throw "Path is outside the expected base directory: $target"
    }
    return $target.Substring($base.Length + 1)
}

if ($record.PSObject.Properties["storage_profile"] -and $record.storage_profile -eq "minimal") {
    $receiptRelative = [string]$record.production_receipt
    if ($receiptRelative -ne "workspace/production-receipt.json") {
        throw "Minimal Work $WorkId has no canonical production receipt"
    }
    $receiptPath = Join-Path $taskRoot $receiptRelative
    if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw "Minimal Work $WorkId production receipt is missing" }
    $receiptHash = (Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($receiptHash -ne [string]$record.production_receipt_sha256) { throw "Minimal Work $WorkId production receipt changed after review" }
    $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json -ErrorAction Stop
    if ($receipt.schema_version -ne 1 -or $receipt.storage_profile -ne "minimal" -or
        $receipt.work_id -ne [string]$WorkId -or $receipt.status -ne "review" -or $receipt.user_approved -ne $false) {
        throw "Minimal Work $WorkId production receipt does not describe an unaccepted review"
    }
    function Assert-MinimalRecords([object[]]$Records, [string]$Label) {
        foreach ($item in @($Records)) {
            $path = [IO.Path]::GetFullPath((Join-Path $taskRoot ([string]$item.path)))
            $null = Get-SafeRelativePath $taskRoot $path
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "$Label file is missing after review: $($item.path)" }
            $file = Get-Item -LiteralPath $path
            $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
            if ($file.Length -ne [long]$item.bytes -or $hash -ne [string]$item.sha256) { throw "$Label file changed after review: $($item.path)" }
        }
    }
    Assert-MinimalRecords @($receipt.request) "Request"
    Assert-MinimalRecords $receipt.input_materials "Input material"
    Assert-MinimalRecords $receipt.production_files "Production/QA evidence"
    Assert-MinimalRecords $receipt.outputs "Output"

    $inputRoot = Join-Path $taskRoot "input"
    $currentInputs = @("input/request.md")
    foreach ($folder in @("source", "reference")) {
        $folderPath = Join-Path $inputRoot $folder
        if (Test-Path -LiteralPath $folderPath -PathType Container) {
            $currentInputs += @(Get-ChildItem -LiteralPath $folderPath -Recurse -File -Force | ForEach-Object {
                (Get-SafeRelativePath $taskRoot $_.FullName).Replace('\', '/')
            })
        }
    }
    $expectedInputs = @(@($receipt.request.path) + @($receipt.input_materials | ForEach-Object { [string]$_.path }) | Sort-Object -Unique)
    $currentInputs = @($currentInputs | Sort-Object -Unique)
    if (@(Compare-Object $expectedInputs $currentInputs).Count) { throw "Minimal Work input material set changed after review; prepare a new review" }

    $workspaceRoot = Join-Path $taskRoot "workspace"
    $currentProduction = @(
        Get-ChildItem -LiteralPath $workspaceRoot -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $relative = (Get-SafeRelativePath $workspaceRoot $_.FullName).Replace('\', '/')
            $parts = @($relative -split '/'); $extension = $_.Extension.ToLowerInvariant()
            $excludedSegments = @($parts | Where-Object { $_ -in @("conversations", "review-history", "intermediate", "cache", "node_modules", ".venv", "venv", "data", "tmp") })
            $excluded = $excludedSegments.Count -gt 0 -or
                        $_.Name -match '^\.env($|\.)' -or
                        $_.Name -in @("production-receipt.json", "worklog.md", "assistant-context.json", "knowledge-context.json", "startup-diagnostics.json", "launcher-startup-diagnostics.json", "retrospective-draft.md", "knowledge-candidate.md") -or
                        $relative -match '^studio-jobs/[^/]+/planning/intake-requests/'
            $codeOrConfig = $extension -in @(".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".sh", ".ps1", ".json", ".yaml", ".yml", ".toml", ".ini", ".css", ".html")
            $rootFile = $parts.Count -eq 1 -and $codeOrConfig
            $asset = $parts[0] -eq "assets" -and $extension -in @(".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif")
            $eligible = $parts[0] -in @("scripts", "qa", "quality-observations") -or $relative -in @("aippt.sh", "aippt.ps1", ".studio-root", "runtime-environment.json") -or
                        $rootFile -or $asset -or
                        $relative -match '^studio-jobs/[^/]+/planning/' -or
                        $relative -match '^studio-jobs/[^/]+/assets/(asset-plan\.json|asset-ledger\.json|acquired/)' -or
                        $relative -match '^studio-jobs/[^/]+/(qa|quality|reviews)/'
            if (-not $excluded -and $eligible) { "workspace/$relative" }
        } | Sort-Object -Unique
    )
    $expectedProduction = @($receipt.production_files | ForEach-Object { ([string]$_.path).Replace('\', '/') } | Sort-Object -Unique)
    if (@(Compare-Object $expectedProduction $currentProduction).Count) { throw "Minimal Work production/QA file set changed after review; prepare a new review" }

    $expectedOutputs = @($receipt.outputs | ForEach-Object { ([string]$_.path).Replace('\', '/') } | Sort-Object)
    $actualOutputs = @(
        Get-ChildItem -LiteralPath (Join-Path $taskRoot "output") -File -Recurse -Force |
            ForEach-Object { (Get-SafeRelativePath $taskRoot $_.FullName).Replace('\', '/') } | Sort-Object
    )
    if (@(Compare-Object $expectedOutputs $actualOutputs).Count) { throw "Minimal Work output file set changed after review; prepare a new review" }

    $completedAt = (Get-Date).ToString("o")
    $record | Add-Member -Force -NotePropertyName "status" -NotePropertyValue "completed"
    $record | Add-Member -Force -NotePropertyName "completed_at" -NotePropertyValue $completedAt
    $record | Add-Member -Force -NotePropertyName "user_approved" -NotePropertyValue $true
    $record | Add-Member -Force -NotePropertyName "approval_note" -NotePropertyValue $ApprovalNote.Trim()
    $resolvedMinimalKnowledge = @()
    if ($KnowledgeStatus -eq "promoted") {
        if (-not $KnowledgeFiles.Count) { throw "KnowledgeStatus promoted requires at least one existing Studio knowledge file" }
        foreach ($item in $KnowledgeFiles) {
            $path = if ([IO.Path]::IsPathRooted($item)) { [IO.Path]::GetFullPath($item) } else { [IO.Path]::GetFullPath((Join-Path $repoRoot $item)) }
            if (-not $path.StartsWith($repoRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::Ordinal)) {
                throw "Knowledge file is outside the AI PPT workspace: $path"
            }
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Knowledge file does not exist: $path" }
            $resolvedMinimalKnowledge += [ordered]@{ path = (Get-SafeRelativePath $repoRoot $path).Replace('\', '/'); bytes = (Get-Item -LiteralPath $path).Length; sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
        }
    }
    elseif ($KnowledgeFiles.Count) { throw "KnowledgeFiles must be empty when KnowledgeStatus is no-new-knowledge" }
    $record | Add-Member -Force -NotePropertyName "knowledge_status" -NotePropertyValue $KnowledgeStatus
    $record | Add-Member -Force -NotePropertyName "knowledge_files" -NotePropertyValue $resolvedMinimalKnowledge
    $record | Add-Member -Force -NotePropertyName "maintenance_status" -NotePropertyValue "skipped_minimal_profile"
    if ($record.PSObject.Properties["required_confirmation_phrase"]) { $record.PSObject.Properties.Remove("required_confirmation_phrase") }
    Write-WorkLifecycleRecord -Stream $lifecycleStream -Text ($record | ConvertTo-Json -Depth 8)
    [pscustomobject]@{ work_id = [string]$WorkId; status = "completed"; completed_at = $completedAt; user_approved = $true; production_receipt_sha256 = $receiptHash; maintenance_status = "skipped_minimal_profile" } | ConvertTo-Json -Depth 4
    return
}

$required = @(
    "workspace/completion-report.md",
    "workspace/qa/final-output-manifest.json",
    "workspace/knowledge-candidate.md",
    "workspace/worklog.md"
)
foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskRoot $relative) -PathType Leaf)) {
        throw "Work $WorkId is missing closure evidence: $relative"
    }
}

$candidatePath = Join-Path $taskRoot "workspace/knowledge-candidate.md"
$candidate = Get-Content -LiteralPath $candidatePath -Raw
if ($candidate -match "待评估|需重新评估|\bTODO\b|\bTBD\b") {
    throw "Knowledge candidate still contains unresolved placeholders"
}

$manifestPath = Join-Path $taskRoot "workspace/qa/final-output-manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.status -ne "review" -or $manifest.work_id -ne [string]$WorkId) {
    throw "Final output manifest does not belong to the current review"
}
function Assert-ManifestRecords([object[]]$Records, [string]$Label) {
    if ($null -eq $Records) { return }
    foreach ($item in @($Records)) {
        $path = [System.IO.Path]::GetFullPath((Join-Path $taskRoot ([string]$item.path)))
        $null = Get-SafeRelativePath $taskRoot $path
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "$Label file is missing after review: $($item.path)"
        }
        $file = Get-Item -LiteralPath $path
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        if ($file.Length -ne [long]$item.bytes -or $hash -ne [string]$item.sha256) {
            throw "$Label file changed after review: $($item.path)"
        }
    }
}
Assert-ManifestRecords $manifest.outputs "Output"
Assert-ManifestRecords $manifest.adapter_evidence "Adapter evidence"
Assert-ManifestRecords $manifest.asset_evidence "Asset evidence"
Assert-ManifestRecords $manifest.knowledge_context "Knowledge context"
if ($manifest.PSObject.Properties["closure_evidence"]) {
    Assert-ManifestRecords $manifest.closure_evidence "Closure evidence"
}
Assert-ManifestRecords $manifest.input.source "Source"
Assert-ManifestRecords $manifest.input.reference "Reference"
$expectedOutputs = @($manifest.outputs | ForEach-Object { ([string]$_.path).Replace('\', '/') } | Sort-Object)
$actualOutputs = @(Get-ChildItem -LiteralPath (Join-Path $taskRoot "output") -File -Recurse -Force | ForEach-Object {
    (Get-SafeRelativePath $taskRoot $_.FullName).Replace('\', '/')
} | Sort-Object)
if (@(Compare-Object $expectedOutputs $actualOutputs).Count) {
    throw "Output file set changed after review; prepare a new review"
}

$resolvedKnowledgeFiles = @()
if ($KnowledgeStatus -eq "promoted") {
    if (-not $KnowledgeFiles.Count) {
        throw "KnowledgeStatus promoted requires at least one Studio knowledge file"
    }
    foreach ($item in $KnowledgeFiles) {
        $path = if ([System.IO.Path]::IsPathRooted($item)) {
            [System.IO.Path]::GetFullPath($item)
        }
        else {
            [System.IO.Path]::GetFullPath((Join-Path $repoRoot $item))
        }
        if (-not $path.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Knowledge file is outside the AI PPT workspace: $path"
        }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Knowledge file does not exist: $path"
        }
        $resolvedKnowledgeFiles += (Get-SafeRelativePath $repoRoot $path).Replace('\', '/')
    }
}
elseif ($KnowledgeFiles.Count) {
    throw "KnowledgeFiles must be empty when KnowledgeStatus is no-new-knowledge"
}

$taskKnowledgeRootFull = [System.IO.Path]::GetFullPath($TaskKnowledgeRoot)
$taskKnowledgeEntries = Join-Path $taskKnowledgeRootFull "entries"
$taskKnowledgeIndex = Join-Path $taskKnowledgeRootFull "INDEX.md"
if (-not (Test-Path -LiteralPath $taskKnowledgeEntries -PathType Container)) {
    throw "Task knowledge entries directory is missing: $taskKnowledgeEntries"
}
if (-not (Test-Path -LiteralPath $taskKnowledgeIndex -PathType Leaf)) {
    throw "Task knowledge index is missing: $taskKnowledgeIndex"
}
$taskKnowledgeMatches = @(
    Get-ChildItem -LiteralPath $taskKnowledgeEntries -Recurse -File -Filter "work-$WorkId-*.md" |
        Sort-Object FullName
)
if ($taskKnowledgeMatches.Count -ne 1) {
    throw "Work $WorkId requires exactly one independent task knowledge entry; found $($taskKnowledgeMatches.Count) under $taskKnowledgeEntries"
}
$taskKnowledgeEntry = $taskKnowledgeMatches[0]
$taskKnowledgeContent = Get-Content -LiteralPath $taskKnowledgeEntry.FullName -Raw
if ($taskKnowledgeContent -match "待补充|待复盘|\bTODO\b|\bTBD\b") {
    throw "Task knowledge entry still contains unresolved placeholders: $($taskKnowledgeEntry.FullName)"
}
if ($record.PSObject.Properties["closure_schema_version"] -and [int]$record.closure_schema_version -ge 2) {
    foreach ($heading in @("任务目标与完成结果", "输入语义", "用户反馈与对应修改", "失败、诊断与恢复", "最终验证", "可复用经验", "不应推广的任务事实", "证据位置")) {
        $section = [regex]::Match($taskKnowledgeContent, '(?ms)^##\s+' + [regex]::Escape($heading) + '\s*\r?\n(.*?)(?=^##\s+|\z)')
        if (-not $section.Success -or $section.Groups[1].Value.Trim().Length -lt 10) {
            throw "Task retrospective requires a substantive section: $heading"
        }
    }
}
$taskKnowledgeStatusPattern = '(?m)^-\s*状态：\s*(review|completed)\s*$'
if ($taskKnowledgeContent -notmatch $taskKnowledgeStatusPattern) {
    throw "Task knowledge entry must declare review or completed status: $($taskKnowledgeEntry.FullName)"
}
$taskKnowledgeRelative = (Get-SafeRelativePath $taskKnowledgeRootFull $taskKnowledgeEntry.FullName).Replace('\', '/')
$taskKnowledgeIndexText = Get-Content -LiteralPath $taskKnowledgeIndex -Raw
if ($taskKnowledgeIndexText -notmatch [regex]::Escape($taskKnowledgeRelative)) {
    throw "Task knowledge index does not reference: $taskKnowledgeRelative"
}
$taskKnowledgeIndexLines = @($taskKnowledgeIndexText -split "`r?`n")
$taskKnowledgeIndexMatches = @(
    for ($lineIndex = 0; $lineIndex -lt $taskKnowledgeIndexLines.Count; $lineIndex++) {
        if ($taskKnowledgeIndexLines[$lineIndex] -match [regex]::Escape($taskKnowledgeRelative)) {
            $lineIndex
        }
    }
)
if ($taskKnowledgeIndexMatches.Count -ne 1) {
    throw "Task knowledge index must contain exactly one row for: $taskKnowledgeRelative"
}
$knowledgeIndexLine = $taskKnowledgeIndexLines[$taskKnowledgeIndexMatches[0]]
if ($knowledgeIndexLine -notmatch '\|\s*(review|completed)\s*\|') {
    throw "Task knowledge index row must declare review or completed status: $taskKnowledgeRelative"
}
$taskKnowledgeLocator = if ($taskKnowledgeEntry.FullName.StartsWith("$($repoRoot.TrimEnd([IO.Path]::DirectorySeparatorChar))$([IO.Path]::DirectorySeparatorChar)", [System.StringComparison]::Ordinal)) {
    (Get-SafeRelativePath $repoRoot $taskKnowledgeEntry.FullName).Replace('\', '/')
}
else {
    $taskKnowledgeEntry.FullName
}

$taskKnowledgeContent = $taskKnowledgeContent -replace $taskKnowledgeStatusPattern, '- 状态：completed'
$taskKnowledgeContent | Set-Content -LiteralPath $taskKnowledgeEntry.FullName -Encoding utf8
$taskKnowledgeEntrySha256 = (Get-FileHash -LiteralPath $taskKnowledgeEntry.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$taskKnowledgeIndexLines[$taskKnowledgeIndexMatches[0]] = $knowledgeIndexLine -replace '\|\s*review\s*\|', '| completed |'
($taskKnowledgeIndexLines -join "`r`n") | Set-Content -LiteralPath $taskKnowledgeIndex -Encoding utf8

$completedAt = (Get-Date).ToString("o")
function Set-RecordProperty([object]$Object, [string]$Name, [object]$Value) {
    if ($Object.PSObject.Properties[$Name]) {
        $Object.$Name = $Value
    }
    else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}
Set-RecordProperty $record "status" "completed"
Set-RecordProperty $record "completed_at" $completedAt
Set-RecordProperty $record "user_approved" $true
Set-RecordProperty $record "approval_note" $ApprovalNote.Trim()
Set-RecordProperty $record "knowledge_status" $KnowledgeStatus
Set-RecordProperty $record "knowledge_files" $resolvedKnowledgeFiles
Set-RecordProperty $record "task_knowledge_entry" $taskKnowledgeLocator
Set-RecordProperty $record "task_knowledge_entry_sha256" $taskKnowledgeEntrySha256
if ($record.PSObject.Properties["required_confirmation_phrase"]) {
    $record.PSObject.Properties.Remove("required_confirmation_phrase")
}
Write-WorkLifecycleRecord -Stream $lifecycleStream -Text ($record | ConvertTo-Json -Depth 8)

@"

## $completedAt · 用户确认结束

- 用户确认：$($ApprovalNote.Trim())
- 知识处理：$KnowledgeStatus
- 写入 Studio：$(if ($resolvedKnowledgeFiles.Count) { $resolvedKnowledgeFiles -join ', ' } else { '无新的通用知识，原因已写入 knowledge-candidate.md' })
- 独立任务知识：$taskKnowledgeLocator
- Work 状态：completed
"@ | Add-Content -LiteralPath (Join-Path $taskRoot "workspace/worklog.md") -Encoding utf8

@"

## 正式结束

- 用户确认：$($ApprovalNote.Trim())
- 完成时间：$completedAt
- 知识处理：$KnowledgeStatus
- 独立任务知识：$taskKnowledgeLocator
"@ | Add-Content -LiteralPath (Join-Path $taskRoot "workspace/completion-report.md") -Encoding utf8

# Completion is durable before the optional refresh; retryable maintenance errors
# must not pretend that the already recorded user acceptance disappeared.
$maintenanceStatus = "ok"
try {
    . (Join-Path $PSScriptRoot "studio-runtime.ps1")
    $studioPython = Get-StudioPython $repoRoot
    if ($root -eq (Join-Path $repoRoot "work")) {
        & $studioPython -m aippt_studio.maintenance sync-review --repo-root $repoRoot --work-dir $taskRoot | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Project-review completion synchronization failed" }
        & $studioPython -m aippt_studio.maintenance run --repo-root $repoRoot --local-only --quiet
        if ($LASTEXITCODE -ne 0) { throw "Knowledge index refresh failed" }
    }
}
catch {
    $maintenanceStatus = "retry_required"
    Write-Warning "Work completed; automatic archive/index refresh will retry: $($_.Exception.Message)"
}

[pscustomobject]@{
    work_id = [string]$WorkId
    status = "completed"
    completed_at = $completedAt
    user_approved = $true
    knowledge_status = $KnowledgeStatus
    knowledge_files = $resolvedKnowledgeFiles
    task_knowledge_entry = $taskKnowledgeLocator
    task_knowledge_entry_sha256 = $taskKnowledgeEntrySha256
    maintenance_status = $maintenanceStatus
} | ConvertTo-Json -Depth 4
}
finally {
    Close-WorkLifecycleRecord -Stream $lifecycleStream
}
