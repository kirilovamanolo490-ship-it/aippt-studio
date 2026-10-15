[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$WorkId,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Reason,
    [string]$WorkRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) "work"),
    [string]$TaskKnowledgeRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) "任务知识库")
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($WorkRoot)
if (-not $PSBoundParameters.ContainsKey("TaskKnowledgeRoot") -and $PSBoundParameters.ContainsKey("WorkRoot")) {
    $TaskKnowledgeRoot = Join-Path (Split-Path $root -Parent) "任务知识库"
}
$taskRoot = Join-Path $root ([string]$WorkId)
$recordPath = Join-Path $taskRoot "work.json"
if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
    throw "Work record is missing: $recordPath"
}
. (Join-Path $PSScriptRoot "work-lifecycle-lock.ps1")
$lifecycleStream = Open-WorkLifecycleRecord -RecordPath $recordPath
try {
$record = (Read-WorkLifecycleRecord -Stream $lifecycleStream) | ConvertFrom-Json
if ($record.status -notin @("review", "completed")) {
    throw "Only a Work in review or completed can be reopened; current status: $($record.status)"
}
$reopenedAt = (Get-Date).ToString("o")
$reasonText = $Reason.Trim()
if ($record.PSObject.Properties["storage_profile"] -and $record.storage_profile -eq "minimal") {
    $record | Add-Member -Force -NotePropertyName "status" -NotePropertyValue "in_progress"
    $record | Add-Member -Force -NotePropertyName "user_approved" -NotePropertyValue $false
    $record | Add-Member -Force -NotePropertyName "knowledge_status" -NotePropertyValue "not_started"
    $record | Add-Member -Force -NotePropertyName "review_reopened_at" -NotePropertyValue $reopenedAt
    $record | Add-Member -Force -NotePropertyName "review_reopen_reason" -NotePropertyValue $reasonText
    foreach ($propertyName in @("completed_at", "approval_note", "production_receipt", "production_receipt_sha256", "review_prepared_at", "maintenance_status", "required_confirmation_phrase")) {
        if ($record.PSObject.Properties[$propertyName]) { $record.PSObject.Properties.Remove($propertyName) }
    }
    $record | Add-Member -Force -NotePropertyName "knowledge_files" -NotePropertyValue @()
    Write-WorkLifecycleRecord -Stream $lifecycleStream -Text ($record | ConvertTo-Json -Depth 8)
    [pscustomobject]@{ work_id = [string]$WorkId; status = "in_progress"; reopened_at = $reopenedAt; reason = $reasonText; storage_profile = "minimal" } | ConvertTo-Json -Depth 3
    return
}
$snapshotDir = Join-Path $taskRoot ("workspace/review-history/reopen-" + (Get-Date -Format "yyyyMMdd-HHmmss-fffffff"))
[IO.Directory]::CreateDirectory($snapshotDir) | Out-Null
Copy-Item -LiteralPath $recordPath -Destination (Join-Path $snapshotDir "work.json")
foreach ($relative in @("workspace/completion-report.md", "workspace/qa/final-output-manifest.json", "workspace/qa/closure-evidence.json", "workspace/knowledge-candidate.md")) {
    $path = Join-Path $taskRoot $relative
    if (Test-Path -LiteralPath $path -PathType Leaf) { Copy-Item -LiteralPath $path -Destination $snapshotDir }
}

function Set-RecordProperty([object]$Object, [string]$Name, [object]$Value) {
    if ($Object.PSObject.Properties[$Name]) {
        $Object.$Name = $Value
    }
    else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

Set-RecordProperty $record "status" "in_progress"
Set-RecordProperty $record "user_approved" $false
Set-RecordProperty $record "knowledge_status" "not_started"
Set-RecordProperty $record "review_reopened_at" $reopenedAt
Set-RecordProperty $record "review_reopen_reason" $reasonText
if ($record.PSObject.Properties["required_confirmation_phrase"]) {
    $record.PSObject.Properties.Remove("required_confirmation_phrase")
}
Write-WorkLifecycleRecord -Stream $lifecycleStream -Text ($record | ConvertTo-Json -Depth 8)

# A reopened case must stop contributing accepted lessons immediately.
$entries = @(Get-ChildItem -LiteralPath (Join-Path $TaskKnowledgeRoot "entries") -Recurse -File -Filter "work-$WorkId-*.md" -ErrorAction SilentlyContinue)
if ($entries.Count -eq 1) {
    $entryText = Get-Content -LiteralPath $entries[0].FullName -Raw
    $entryText -replace '(?m)^-\s*状态：\s*(review|completed)\s*$', '- 状态：in_progress' | Set-Content -LiteralPath $entries[0].FullName -Encoding utf8
    $index = Join-Path $TaskKnowledgeRoot "INDEX.md"
    if (Test-Path -LiteralPath $index -PathType Leaf) {
        $indexText = Get-Content -LiteralPath $index -Raw
        $pattern = '(?m)^(\|\s*' + $WorkId + '\s*\|[^\r\n]*?\|\s*)(review|completed)(\s*\|)'
        $indexText -replace $pattern, '${1}in_progress${3}' | Set-Content -LiteralPath $index -Encoding utf8
    }
}

$notice = @"

## $reopenedAt · 结项审阅已重新打开

- 原因：$reasonText
- 状态：in_progress
- 旧完成报告和输出清单仅作为历史快照，不再代表当前交付。
- 后续必须重新运行 prepare-work-close.ps1，并再次获得用户明确确认。
"@

$worklog = Join-Path $taskRoot "workspace/worklog.md"
if (Test-Path -LiteralPath $worklog -PathType Leaf) {
    $notice | Add-Content -LiteralPath $worklog -Encoding utf8
}
foreach ($reportPath in @(
    (Join-Path $taskRoot "workspace/completion-report.md"),
    (Join-Path $taskRoot "output/工作完成报告.md")
)) {
    if (Test-Path -LiteralPath $reportPath -PathType Leaf) {
        $notice | Add-Content -LiteralPath $reportPath -Encoding utf8
    }
}
$candidatePath = Join-Path $taskRoot "workspace/knowledge-candidate.md"
if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
@"

## 需重新评估

Work 已因“$reasonText”重新打开。旧知识候选不得用于完成门禁；应依据新的构建、QA 和用户反馈重新评估，并删除本节后再结项。
"@ | Add-Content -LiteralPath $candidatePath -Encoding utf8
}

[pscustomobject]@{
    work_id = [string]$WorkId
    status = "in_progress"
    reopened_at = $reopenedAt
    reason = $reasonText
} | ConvertTo-Json -Depth 3
}
finally {
    Close-WorkLifecycleRecord -Stream $lifecycleStream
}
