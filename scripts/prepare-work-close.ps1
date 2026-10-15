[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$WorkId,
    [string]$Summary = "",
    [string]$WorkRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) "work")
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($WorkRoot)
$taskRoot = Join-Path $root ([string]$WorkId)
$recordPath = Join-Path $taskRoot "work.json"
if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
    throw "Work record is missing: $recordPath"
}
. (Join-Path $PSScriptRoot "work-lifecycle-lock.ps1")
$lifecycleStream = Open-WorkLifecycleRecord -RecordPath $recordPath
try {
foreach ($relative in @("input/source", "input/reference", "workspace", "output")) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskRoot $relative) -PathType Container)) {
        throw "Work $WorkId does not follow the standard structure; missing: $relative"
    }
}

$record = (Read-WorkLifecycleRecord -Stream $lifecycleStream) | ConvertFrom-Json
if ($record.status -eq "completed") {
    throw "Work $WorkId is already completed"
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

function Get-RelativeFileRecords([string]$BasePath, [string]$Prefix) {
    return @(
        Get-ChildItem -LiteralPath $BasePath -Recurse -File -Force -ErrorAction SilentlyContinue |
            Sort-Object FullName |
            ForEach-Object {
                [ordered]@{
                    path = "$Prefix/$((Get-SafeRelativePath $BasePath $_.FullName).Replace('\', '/'))"
                    bytes = $_.Length
                    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
                }
            }
    )
}

function Test-EmptyJsonArrayProperty([object]$Object, [string]$Name, [bool]$Required) {
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return -not $Required }
    if ($property.Value -isnot [System.Array]) { return $false }
    return $property.Value.Count -eq 0
}

$record = (Read-WorkLifecycleRecord -Stream $lifecycleStream) | ConvertFrom-Json
if ($record.status -eq "completed") { throw "Work $WorkId is already completed" }
if ($record.PSObject.Properties["storage_profile"] -and $record.storage_profile -eq "minimal") {
    $outputDir = Join-Path $taskRoot "output"
    $outputs = @(Get-RelativeFileRecords $outputDir "output")
    if (-not $outputs.Count) { throw "Minimal Work $WorkId cannot enter review because output/ has no deliverable" }
    $repoRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
    $qaDir = Join-Path $taskRoot "workspace/qa/output-preflight"
    $presentationOutputs = @(Get-ChildItem -LiteralPath $outputDir -Recurse -File -Force | Where-Object { $_.Extension -ieq ".pptx" } | Sort-Object FullName)
    $qaPreflights = @()
    if ($presentationOutputs.Count) {
        . (Join-Path $PSScriptRoot "studio-runtime.ps1")
        $studioPython = Get-StudioPython $repoRoot
    }
    foreach ($presentation in $presentationOutputs) {
        $hash = (Get-FileHash -LiteralPath $presentation.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $preflightPath = Join-Path $qaDir ("current-{0}.json" -f $hash)
        [IO.Directory]::CreateDirectory($qaDir) | Out-Null
        $policyPath = Join-Path $taskRoot ("workspace/qa/output-quality-policies/{0}.json" -f $hash)
        $preflightArgs = @("-m", "aippt_studio", "output-preflight", "--work-dir", $taskRoot,
                           "--source", $presentation.FullName, "--output", $preflightPath,
                           "--record-observation", "--json")
        if (Test-Path -LiteralPath $policyPath -PathType Leaf) { $preflightArgs += @("--policy", $policyPath) }
        & $studioPython @preflightArgs | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Minimal Work $WorkId PPTX preflight failed or needs review: $($presentation.FullName)" }
        $qaPreflights += [ordered]@{ path = "workspace/qa/output-preflight/current-$hash.json"; bytes = (Get-Item -LiteralPath $preflightPath).Length; sha256 = (Get-FileHash -LiteralPath $preflightPath -Algorithm SHA256).Hash }
    }
    $workspaceRoot = Join-Path $taskRoot "workspace"
    $productionFiles = @(
        Get-ChildItem -LiteralPath $workspaceRoot -Recurse -File -Force -ErrorAction SilentlyContinue |
            Sort-Object FullName | ForEach-Object {
                $relative = (Get-SafeRelativePath $workspaceRoot $_.FullName).Replace('\', '/')
                $parts = @($relative -split '/')
                $extension = $_.Extension.ToLowerInvariant()
                $excludedSegments = @($parts | Where-Object { $_ -in @("conversations", "review-history", "intermediate", "cache", "node_modules", ".venv", "venv", "data", "tmp") })
                $excluded = $excludedSegments.Count -gt 0 -or
                            $_.Name -match '^\.env($|\.)' -or
                            $_.Name -in @("production-receipt.json", "worklog.md", "assistant-context.json", "knowledge-context.json", "startup-diagnostics.json", "launcher-startup-diagnostics.json", "retrospective-draft.md", "knowledge-candidate.md") -or
                            $relative -match '^studio-jobs/[^/]+/planning/intake-requests/'
                $codeOrConfig = $extension -in @(".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".sh", ".ps1", ".json", ".yaml", ".yml", ".toml", ".ini", ".css", ".html")
                $workspaceRootFile = $parts.Count -eq 1 -and $codeOrConfig
                $workspaceAsset = $parts[0] -eq "assets" -and $extension -in @(".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif")
                $isProduction = $parts[0] -in @("scripts", "qa", "quality-observations") -or
                                $relative -in @("aippt.sh", "aippt.ps1", ".studio-root", "runtime-environment.json") -or
                                $workspaceRootFile -or $workspaceAsset -or
                                $relative -match '^studio-jobs/[^/]+/planning/' -or
                                $relative -match '^studio-jobs/[^/]+/assets/(asset-plan\.json|asset-ledger\.json|acquired/)' -or
                                $relative -match '^studio-jobs/[^/]+/(qa|quality|reviews)/'
                if (-not $excluded -and $isProduction) {
                    [ordered]@{ path = "workspace/$relative"; bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
                }
            }
    )
    $inputFiles = @()
    $inputFiles += @(Get-RelativeFileRecords (Join-Path $taskRoot "input/source") "input/source")
    $inputFiles += @(Get-RelativeFileRecords (Join-Path $taskRoot "input/reference") "input/reference")
    $requestPath = Join-Path $taskRoot "input/request.md"
    if (-not (Test-Path -LiteralPath $requestPath -PathType Leaf)) { throw "Minimal Work $WorkId is missing input/request.md" }
    $qaReceiptFiles = @($productionFiles | Where-Object { $_.path -match '^workspace/(qa/|quality-observations/|studio-jobs/[^/]+/(qa|quality|reviews)/)' })
    $qaStatus = if ($presentationOutputs.Count) { "passed" } elseif ($qaReceiptFiles.Count) { "captured_not_assessed" } else { "not_applicable" }
    $receipt = [ordered]@{
        schema_version = 1; work_id = [string]$WorkId; storage_profile = "minimal"
        prepared_at = (Get-Date).ToString("o"); status = "review"; user_approved = $false
        request = [ordered]@{ path = "input/request.md"; bytes = (Get-Item -LiteralPath $requestPath).Length; sha256 = (Get-FileHash -LiteralPath $requestPath -Algorithm SHA256).Hash }
        input_materials = $inputFiles; production_files = $productionFiles; outputs = $outputs
        qa = [ordered]@{ status = $qaStatus; pptx_preflights = $qaPreflights; captured_receipts = $qaReceiptFiles }
    }
    $receiptPath = Join-Path $taskRoot "workspace/production-receipt.json"
    $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $receiptPath -Encoding utf8
    $receiptHash = (Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($record.PSObject.Properties["completed_at"]) { $record.PSObject.Properties.Remove("completed_at") }
    $record.status = "review"
    $record | Add-Member -Force -NotePropertyName "user_approved" -NotePropertyValue $false
    $record | Add-Member -Force -NotePropertyName "production_receipt" -NotePropertyValue "workspace/production-receipt.json"
    $record | Add-Member -Force -NotePropertyName "production_receipt_sha256" -NotePropertyValue $receiptHash
    $record | Add-Member -Force -NotePropertyName "review_prepared_at" -NotePropertyValue $receipt.prepared_at
    Write-WorkLifecycleRecord -Stream $lifecycleStream -Text ($record | ConvertTo-Json -Depth 8)
    [pscustomobject]@{ work_id = [string]$WorkId; status = "review"; receipt = "workspace/production-receipt.json"; receipt_sha256 = $receiptHash; output_count = $outputs.Count; qa_status = $qaStatus } | ConvertTo-Json -Depth 4
    return
}
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot "workspace/qa") -PathType Container)) { throw "Work $WorkId does not follow the standard structure; missing: workspace/qa" }

$sourceDir = Join-Path $taskRoot "input/source"
$referenceDir = Join-Path $taskRoot "input/reference"
$outputDir = Join-Path $taskRoot "output"
$sourceFiles = @(Get-RelativeFileRecords $sourceDir "input/source")
$referenceFiles = @(Get-RelativeFileRecords $referenceDir "input/reference")
$studioJobsDir = Join-Path $taskRoot "workspace/studio-jobs"
$repoRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
. (Join-Path $PSScriptRoot "studio-runtime.ps1")
$studioPython = Get-StudioPython $repoRoot
$toolEvidenceJson = & $studioPython -m aippt_studio.tool_evidence --work-dir $taskRoot --summary (Join-Path $taskRoot "workspace/qa/tool-execution-summary.json")
if ($LASTEXITCODE -ne 0) { throw "Tool evidence collection failed; preserve existing calls and inspect the error before review" }
$toolEvidence = ($toolEvidenceJson -join "`n") | ConvertFrom-Json
$adapterEvidence = @($toolEvidence.records)
$assetEvidence = @(
    if (Test-Path -LiteralPath $studioJobsDir -PathType Container) {
        Get-ChildItem -LiteralPath $studioJobsDir -Recurse -File -Force |
            Where-Object {
                $_.Name -in @("asset-plan.json", "asset-ledger.json") -or
                $_.FullName -match "[\\/]assets[\\/]acquired[\\/]"
            } |
            Sort-Object FullName |
            ForEach-Object {
                [ordered]@{
                    path = "workspace/studio-jobs/$((Get-SafeRelativePath $studioJobsDir $_.FullName).Replace('\', '/'))"
                    bytes = $_.Length
                    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
                }
            }
    }
)
$knowledgeContextEvidence = @(
    $knowledgeContextPath = Join-Path $taskRoot "workspace/knowledge-context.json"
    if (Test-Path -LiteralPath $knowledgeContextPath -PathType Leaf) {
        $knowledgeContextFile = Get-Item -LiteralPath $knowledgeContextPath
        [ordered]@{
            path = "workspace/knowledge-context.json"
            bytes = $knowledgeContextFile.Length
            sha256 = (Get-FileHash -LiteralPath $knowledgeContextFile.FullName -Algorithm SHA256).Hash
        }
    }
)
$deliverableFiles = @(
    Get-ChildItem -LiteralPath $outputDir -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "工作完成报告.md" }
)
if (-not $deliverableFiles.Count) {
    throw "Work $WorkId cannot enter review because output/ has no deliverable"
}

# A review must describe the current deliverable bytes. Historical QA reports
# may refer to an earlier copy, and a manual file replacement can bypass a
# Studio job's own final gate. Check every PPTX now, before changing Work state.
$presentationOutputs = @($deliverableFiles | Where-Object { $_.Extension -ieq ".pptx" } | Sort-Object FullName)
$preflightDir = Join-Path $taskRoot "workspace/qa/output-preflight"
$preflightRunStamp = Get-Date -Format "yyyyMMddTHHmmssfffffff"
for ($index = 0; $index -lt $presentationOutputs.Count; $index++) {
    $presentation = $presentationOutputs[$index]
    $currentHash = (Get-FileHash -LiteralPath $presentation.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $preflightPath = Join-Path $preflightDir ("current-{0:D2}-{1}-{2}.json" -f ($index + 1), $currentHash.Substring(0, 12), $preflightRunStamp)
    $policyPath = Join-Path $taskRoot ("workspace/qa/output-quality-policies/$currentHash.json")
    $preflightArgs = @("-m", "aippt_studio", "output-preflight", "--work-dir", $taskRoot,
                       "--source", $presentation.FullName, "--output", $preflightPath,
                       "--record-observation", "--json")
    if (Test-Path -LiteralPath $policyPath -PathType Leaf) {
        $preflightArgs += @("--policy", $policyPath)
    }
    & $studioPython @preflightArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path -LiteralPath $preflightPath -PathType Leaf) {
            throw "Work $WorkId current PPTX quality needs review: $($presentation.FullName). Inspect $preflightPath and resolve open issues against this exact file before review."
        }
        throw "Work $WorkId current PPTX preflight failed before a QA report could be written: $($presentation.FullName). Inspect the command error above."
    }
}

# Preserve previous review bytes before generating the next review.
$priorManifest = Join-Path $taskRoot "workspace/qa/final-output-manifest.json"
if (Test-Path -LiteralPath $priorManifest -PathType Leaf) {
    $snapshotDir = Join-Path $taskRoot ("workspace/review-history/" + (Get-Date -Format "yyyyMMdd-HHmmss-fffffff"))
    [IO.Directory]::CreateDirectory($snapshotDir) | Out-Null
    foreach ($relative in @("workspace/completion-report.md", "workspace/qa/final-output-manifest.json", "workspace/qa/closure-evidence.json", "workspace/knowledge-candidate.md")) {
        $priorPath = Join-Path $taskRoot $relative
        if (Test-Path -LiteralPath $priorPath -PathType Leaf) { Copy-Item -LiteralPath $priorPath -Destination $snapshotDir }
    }
}
$closurePath = Join-Path $taskRoot "workspace/qa/closure-evidence.json"
$closureHashBefore = if (Test-Path -LiteralPath $closurePath -PathType Leaf) {
    (Get-FileHash -LiteralPath $closurePath -Algorithm SHA256).Hash.ToLowerInvariant()
} else { $null }
$metadataOnlyPartialAccepted = $false
& $studioPython -m aippt_studio.maintenance closure --work-dir $taskRoot | Out-Null
$closureExitCode = $LASTEXITCODE
if ($closureExitCode -ne 0) {
    $partialMetadataOnly = $false
    $closureHashAfter = if (Test-Path -LiteralPath $closurePath -PathType Leaf) {
        (Get-FileHash -LiteralPath $closurePath -Algorithm SHA256).Hash.ToLowerInvariant()
    } else { $null }
    $freshClosureCapture = $null -ne $closureHashAfter -and $closureHashAfter -ne $closureHashBefore
    if ($freshClosureCapture) {
        try {
            $closureCheck = Get-Content -LiteralPath $closurePath -Raw | ConvertFrom-Json -ErrorAction Stop
            $retentionPath = Join-Path $taskRoot "workspace/conversations/retention-policy.json"
            $retention = if (Test-Path -LiteralPath $retentionPath -PathType Leaf) {
                Get-Content -LiteralPath $retentionPath -Raw | ConvertFrom-Json -ErrorAction Stop
            }
            $bindingStatus = [string]$closureCheck.binding_retry.status
            $partialMetadataOnly = (
                [string]$closureCheck.work_id -eq [string]$WorkId -and
                $closureCheck.status -eq "partial" -and
                $closureCheck.archive_status -eq "metadata_only" -and
                $retention.mode -eq "metadata_only" -and
                $closureCheck.feedback_coverage.status -eq "retention_limited" -and
                $closureCheck.feedback_coverage.retention_limited -eq $true -and
                $closureCheck.conversation_coverage.status -eq "retention_limited" -and
                $closureCheck.conversation_coverage.reason -eq "metadata_only_retention_policy" -and
                $closureCheck.conversation_coverage.local_raw_available -eq $false -and
                (Test-EmptyJsonArrayProperty $closureCheck "event_verification_errors" $true) -and
                (Test-EmptyJsonArrayProperty $closureCheck.feedback_coverage "errors" $false) -and
                (Test-EmptyJsonArrayProperty $closureCheck "errors" $false) -and
                (Test-EmptyJsonArrayProperty $closureCheck.feedback_coverage "warnings" $true) -and
                $bindingStatus -in @("none", "metadata_only", "bound") -and
                (Test-EmptyJsonArrayProperty $closureCheck "archive_results" $true)
            )
        }
        catch {
            $partialMetadataOnly = $false
        }
    }
    if (-not $partialMetadataOnly) {
        throw "Closure evidence capture failed; inspect workspace/qa/closure-evidence.json and workspace/pending-conversation-binding.json before review"
    }
    $metadataOnlyPartialAccepted = $true
}
$closure = Get-Content -LiteralPath $closurePath -Raw | ConvertFrom-Json
if ($closureExitCode -eq 0 -and $closure.status -ne "ready") {
    throw "Closure evidence reported status '$($closure.status)' after a successful exit; only a ready capture can continue through the normal review path"
}
$closureEvidence = @([ordered]@{
    path = "workspace/qa/closure-evidence.json"
    bytes = (Get-Item -LiteralPath $closurePath).Length
    sha256 = (Get-FileHash -LiteralPath $closurePath -Algorithm SHA256).Hash
}) + @($closure.records)

$preparedAt = (Get-Date).ToString("o")
$confirmationPhrase = "确认 Work $WorkId 彻底完成并结项"
$summaryText = if ($Summary.Trim()) { $Summary.Trim() } else { "已完成任务构建、审阅、QA 和交付准备，等待用户最终确认。" }
$deliverableRows = @(
    $deliverableFiles | Sort-Object FullName | ForEach-Object {
        $relative = "output/$((Get-SafeRelativePath $outputDir $_.FullName).Replace('\', '/'))"
        "| ``$relative`` | $($_.Length) | $((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash) |"
    }
)
$closureCompletionLimitations = if ($metadataOnlyPartialAccepted) {
    "用户确认仅关闭 Work 状态；Work 本地原始对话快照不会重建，依赖原始记录的语义复盘和项目复核均受限。"
} else {
    "系统记录用户原话，自动刷新对话证据并处理知识索引；点击 Codex 侧栏归档会触发原始记录备份与已启用的语义学习，不改变作品验收状态。"
}
$report = @"
# Work $WorkId 工作完成报告

## 当前结论

- 任务：$($record.name)
- 客户：$($record.client)
- 状态：review，等待用户明确同意结束
- 准备时间：$preparedAt
- 摘要：$summaryText

## 输入与参考

- 原始内容文件：$($sourceFiles.Count)
- 参考文件：$($referenceFiles.Count)
- 用户要求：input/request.md

## 运作流程

- 完整操作、反馈、修改、失败与 QA：workspace/worklog.md
- 输出哈希清单：workspace/qa/final-output-manifest.json
- 当前PPTX本地预检：workspace/qa/output-preflight/（按当前文件哈希保存；有PPTX时生成）
- 知识提炼候选：workspace/knowledge-candidate.md
- Adapter 调用与清单证据：$($adapterEvidence.Count) 项
- 联网素材、素材计划与来源账本：$($assetEvidence.Count) 项
- 新任务历史知识召回上下文：$($knowledgeContextEvidence.Count) 项
- 原始对话归档状态：$($closure.archive_status)（仅表示本机可获得的对话记录；不表示云端完整记录）
- 闭环证据状态：$($closure.status)。$(if ($closure.status -eq "partial" -and $closure.archive_status -eq "metadata_only") { " retention_mode=metadata_only：原始对话快照与反馈原文不可用；本报告不将缺失证据视为已收集。" })
- 项目复核队列：$(if ($closure.status -eq "partial" -and $closure.archive_status -eq "metadata_only") { "本轮未提交新请求；若已有历史请求，其 closure generation 未由本次 partial evidence 验证，本报告不声明其当前有效。" } else { "结项准备完成后提交。" })
- 对话快照、反馈事件和上下文证据：workspace/qa/closure-evidence.json
- 自动汇集的复盘草稿：workspace/retrospective-draft.md

## 用户可见交付物

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
$($deliverableRows -join "`n")

## 结束门禁

本报告表示交付已准备好供审阅。用户明确表示当前 Work 完成或归档即可进入结项流程，无需记忆固定口令。例如：``$confirmationPhrase``。$closureCompletionLimitations

如果仍需修改，请回复：``继续修改 Work $WorkId：<原因>``。系统会重新打开 Work，旧报告失效。
"@
$workspaceReport = Join-Path $taskRoot "workspace/completion-report.md"
$outputReport = Join-Path $outputDir "工作完成报告.md"
$report | Set-Content -LiteralPath $workspaceReport -Encoding utf8
$report | Set-Content -LiteralPath $outputReport -Encoding utf8

$candidatePath = Join-Path $taskRoot "workspace/knowledge-candidate.md"
if (-not (Test-Path -LiteralPath $candidatePath -PathType Leaf)) {
@"
# Work $WorkId 知识候选

状态：待评估

## 证据

- workspace/completion-report.md
- workspace/worklog.md
- workspace/qa/final-output-manifest.json

## 可复用的新决策

- 待评估

## 新发现的失败模式或踩坑

- 待评估

## 建议写入 Studio 的位置

- 待评估：知识库、专项 reference、核心变更记录，或无需提升。

## 不提升为通用知识的任务特有信息

- 待评估

如果本任务没有新的通用知识，应明确写出原因，不要为了完成流程而制造经验。
"@ | Set-Content -LiteralPath $candidatePath -Encoding utf8
}

$allOutputs = Get-RelativeFileRecords $outputDir "output"
$manifest = [ordered]@{
    schema_version = 1
    work_id = [string]$WorkId
    generated_at = $preparedAt
    status = "review"
    input = [ordered]@{
        source = $sourceFiles
        reference = $referenceFiles
        request = "input/request.md"
    }
    outputs = $allOutputs
    completion_report = "workspace/completion-report.md"
    knowledge_candidate = "workspace/knowledge-candidate.md"
    adapter_evidence = $adapterEvidence
    asset_evidence = $assetEvidence
    knowledge_context = $knowledgeContextEvidence
    closure_evidence = $closureEvidence
}
$manifestPath = Join-Path $taskRoot "workspace/qa/final-output-manifest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8

function Set-RecordProperty([object]$Object, [string]$Name, [object]$Value) {
    if ($Object.PSObject.Properties[$Name]) {
        $Object.$Name = $Value
    }
    else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}
Set-RecordProperty $record "status" "review"
Set-RecordProperty $record "review_prepared_at" $preparedAt
Set-RecordProperty $record "user_approved" $false
Set-RecordProperty $record "knowledge_status" "pending"
Set-RecordProperty $record "completion_report" "workspace/completion-report.md"
Set-RecordProperty $record "output_manifest" "workspace/qa/final-output-manifest.json"
Set-RecordProperty $record "knowledge_candidate" "workspace/knowledge-candidate.md"
Set-RecordProperty $record "required_confirmation_phrase" $confirmationPhrase
if ($record.PSObject.Properties["completed_at"]) {
    $record.PSObject.Properties.Remove("completed_at")
}
Write-WorkLifecycleRecord -Stream $lifecycleStream -Text ($record | ConvertTo-Json -Depth 8)

# The request binds the final manifest and review state. Do not rewrite closure
# evidence here: its bytes are already sealed by that manifest.
if ($root -eq (Join-Path $repoRoot "work")) {
    if (-not $metadataOnlyPartialAccepted) {
        & $studioPython -m aippt_studio.maintenance request-review --repo-root $repoRoot --work-dir $taskRoot | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Project-review request failed after review preparation; retry the request without changing approved evidence" }
    }
}

[pscustomobject]@{
    work_id = [string]$WorkId
    status = "review"
    report = $workspaceReport
    output_report = $outputReport
    manifest = $manifestPath
    knowledge_candidate = $candidatePath
} | ConvertTo-Json -Depth 3
}
finally {
    Close-WorkLifecycleRecord -Stream $lifecycleStream
}
