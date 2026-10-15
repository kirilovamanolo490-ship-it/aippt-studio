param(
    [Parameter(Mandatory = $true)][ValidateSet("Apply", "Probe")][string]$Mode,
    [string]$InputPptx,
    [string]$OutputPptx,
    [string]$ContractPath,
    [string]$BaselinePptx,
    [string]$CandidatePptx,
    [string]$ProbeOutputDir
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)

$msoFalse = 0
$msoTrue = -1
$ppSaveAsOpenXMLPresentation = 24
$ppEffectMorphByObject = 3954
$msoAnimateLevelNone = 0
$effectIds = @{
    "fade" = 10
    "wipe" = 22
    "appear-exit" = 1
    "motion-right" = 149
    "growShrink" = 59
}
$triggerIds = @{
    "onClick" = 1
    "withPrevious" = 2
    "afterPrevious" = 3
}

$script:powerPoint = $null
$script:presentation = $null
$script:currentStage = "input_validation"
$script:workPath = $null
$script:report = [ordered]@{
    mode = $Mode.ToLowerInvariant()
    status = "running"
    classification = $null
    failed_stage = $null
    error = $null
    stages = @()
    slide_count = $null
    output = $null
    renames = @()
    effects = @()
    transitions = @()
    previews = @()
    artifacts = [ordered]@{}
}

function Add-StageResult {
    param([string]$Name, [string]$Status, [string]$Detail = $null)
    $row = [ordered]@{ name = $Name; status = $Status }
    if ($Detail) { $row.detail = $Detail }
    $script:report.stages += $row
}

function Invoke-NativeStage {
    param([Parameter(Mandatory = $true)][string]$Name, [Parameter(Mandatory = $true)][scriptblock]$Action)
    $script:currentStage = $Name
    try {
        $value = & $Action
        Add-StageResult -Name $Name -Status "passed"
        return $value
    } catch {
        Add-StageResult -Name $Name -Status "failed" -Detail $_.Exception.Message
        throw
    }
}

function Release-ComObject {
    param($Value)
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Close-CurrentPresentation {
    if ($script:presentation) {
        try { $script:presentation.Close() } catch {}
        Release-ComObject $script:presentation
        $script:presentation = $null
    }
}

function Get-ShapeByName {
    param([Parameter(Mandatory = $true)]$Slide, [Parameter(Mandatory = $true)][string]$Name)
    for ($index = 1; $index -le $Slide.Shapes.Count; $index++) {
        $shape = $Slide.Shapes.Item($index)
        if ($shape.Name -ceq $Name) { return $shape }
        Release-ComObject $shape
    }
    throw "Shape '$Name' was not found on slide $($Slide.SlideIndex)."
}

function Test-ShapeName {
    param([Parameter(Mandatory = $true)]$Slide, [Parameter(Mandatory = $true)][string]$Name)
    for ($index = 1; $index -le $Slide.Shapes.Count; $index++) {
        $shape = $Slide.Shapes.Item($index)
        $matched = $shape.Name -ceq $Name
        Release-ComObject $shape
        if ($matched) { return $true }
    }
    return $false
}

function Remove-AllEffects {
    param([Parameter(Mandatory = $true)]$Sequence)
    while ($Sequence.Count -gt 0) {
        $effect = $Sequence.Item(1)
        $effect.Delete()
        Release-ComObject $effect
    }
}

function Get-PreviewRoot {
    param([Parameter(Mandatory = $true)][string]$PptxPath)
    $cursor = [IO.DirectoryInfo](Split-Path -Parent $PptxPath)
    while ($cursor) {
        if ($cursor.Name -ieq "build") { return (Join-Path $cursor.FullName "animated-previews") }
        $cursor = $cursor.Parent
    }
    return (Join-Path (Split-Path -Parent $PptxPath) "animated-previews")
}

function New-AnimationPreview {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePptx,
        [Parameter(Mandatory = $true)]$Preview
    )
    $previewRoot = Get-PreviewRoot -PptxPath ([IO.Path]::GetFullPath($OutputPptx))
    New-Item -ItemType Directory -Force -Path $previewRoot | Out-Null
    $finalVideo = Join-Path $previewRoot ("{0}.mp4" -f [string]$Preview.id)
    $token = [Guid]::NewGuid().ToString("N")
    $previewPptx = Join-Path $previewRoot (".{0}.{1}.pptx" -f [string]$Preview.id, $token)
    $temporaryVideo = Join-Path $previewRoot (".{0}.{1}.mp4" -f [string]$Preview.id, $token)
    $previewPresentation = $null
    try {
        Copy-Item -LiteralPath $SourcePptx -Destination $previewPptx -Force
        $previewPresentation = $script:powerPoint.Presentations.Open($previewPptx, $msoFalse, $msoFalse, $msoFalse)
        $wanted = @{}
        foreach ($number in @($Preview.slides)) { $wanted[[int]$number] = $true }
        foreach ($number in @($Preview.slides)) {
            if ([int]$number -gt $previewPresentation.Slides.Count) {
                throw "Preview '$($Preview.id)' requests slide $number but the deck has $($previewPresentation.Slides.Count) slides."
            }
        }
        for ($number = $previewPresentation.Slides.Count; $number -ge 1; $number--) {
            if (-not $wanted.ContainsKey($number)) { $previewPresentation.Slides.Item($number).Delete() }
        }
        $previewPresentation.Save()
        $useTimings = if ([bool]$Preview.use_timings_and_narrations) { $msoTrue } else { $msoFalse }
        $previewPresentation.CreateVideo(
            $temporaryVideo,
            $useTimings,
            [int][math]::Ceiling([double]$Preview.seconds_per_slide),
            [int]$Preview.vertical_resolution,
            [int]$Preview.frames_per_second,
            [int]$Preview.quality
        )
        $watch = [Diagnostics.Stopwatch]::StartNew()
        do {
            Start-Sleep -Milliseconds 500
            $videoStatus = [int]$previewPresentation.CreateVideoStatus
            if ($watch.Elapsed.TotalSeconds -gt [int]$Preview.timeout_seconds) {
                throw "Preview '$($Preview.id)' timed out after $($Preview.timeout_seconds) seconds."
            }
        } while ($videoStatus -eq 1 -or $videoStatus -eq 2)
        if ($videoStatus -ne 3 -or -not (Test-Path -LiteralPath $temporaryVideo)) {
            throw "Preview '$($Preview.id)' failed with CreateVideoStatus=$videoStatus."
        }
        $previewPresentation.Close()
        Release-ComObject $previewPresentation
        $previewPresentation = $null
        Move-Item -LiteralPath $temporaryVideo -Destination $finalVideo -Force
        return [ordered]@{
            id = [string]$Preview.id
            slides = @($Preview.slides | ForEach-Object { [int]$_ })
            path = [IO.Path]::GetFullPath($finalVideo)
            seconds_per_slide = [double]$Preview.seconds_per_slide
        }
    } finally {
        if ($previewPresentation) {
            try { $previewPresentation.Close() } catch {}
            Release-ComObject $previewPresentation
        }
        Remove-Item -LiteralPath $previewPptx -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $temporaryVideo -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-ApplyMode {
    if (-not $InputPptx -or -not $OutputPptx -or -not $ContractPath) {
        throw "Apply mode requires InputPptx, OutputPptx, and ContractPath."
    }
    $inputPath = (Resolve-Path -LiteralPath $InputPptx).Path
    $contractFile = (Resolve-Path -LiteralPath $ContractPath).Path
    $outputPath = [IO.Path]::GetFullPath($OutputPptx)
    if ($inputPath -eq $outputPath) { throw "OutputPptx must not overwrite InputPptx." }
    $contract = Get-Content -LiteralPath $contractFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $outputParent = Split-Path -Parent $outputPath
    New-Item -ItemType Directory -Force -Path $outputParent | Out-Null
    $script:workPath = Join-Path $outputParent (".{0}.native-{1}.pptx" -f [IO.Path]::GetFileNameWithoutExtension($outputPath), [Guid]::NewGuid().ToString("N"))
    Copy-Item -LiteralPath $inputPath -Destination $script:workPath -Force

    Invoke-NativeStage -Name "startup" -Action {
        $script:powerPoint = New-Object -ComObject PowerPoint.Application
    } | Out-Null
    Invoke-NativeStage -Name "candidate_open" -Action {
        $script:presentation = $script:powerPoint.Presentations.Open($script:workPath, $msoFalse, $msoFalse, $msoFalse)
        $script:report.slide_count = [int]$script:presentation.Slides.Count
    } | Out-Null
    Invoke-NativeStage -Name "rename_shapes" -Action {
        foreach ($rename in @($contract.renames)) {
            $slide = $script:presentation.Slides.Item([int]$rename.slide)
            $shape = $null
            try {
                if ([string]$rename.from -cne [string]$rename.to -and (Test-ShapeName -Slide $slide -Name ([string]$rename.to))) {
                    throw "Shape '$($rename.to)' already exists on slide $($rename.slide)."
                }
                $shape = Get-ShapeByName -Slide $slide -Name ([string]$rename.from)
                $shape.Name = [string]$rename.to
                $script:report.renames += [ordered]@{ slide = [int]$rename.slide; from = [string]$rename.from; to = [string]$rename.to }
            } finally {
                Release-ComObject $shape
                Release-ComObject $slide
            }
        }
    } | Out-Null
    Invoke-NativeStage -Name "apply_effects" -Action {
        if ([bool]$contract.replace_existing) {
            $slideNumbers = @($contract.effects | ForEach-Object { [int]$_.slide } | Sort-Object -Unique)
            foreach ($slideNumber in $slideNumbers) {
                $slide = $script:presentation.Slides.Item($slideNumber)
                $sequence = $slide.TimeLine.MainSequence
                Remove-AllEffects -Sequence $sequence
                Release-ComObject $sequence
                Release-ComObject $slide
            }
        }
        foreach ($spec in @($contract.effects)) {
            $slide = $script:presentation.Slides.Item([int]$spec.slide)
            $shape = $null
            $sequence = $null
            $effect = $null
            try {
                $shape = Get-ShapeByName -Slide $slide -Name ([string]$spec.shape)
                $sequence = $slide.TimeLine.MainSequence
                $effect = $sequence.AddEffect($shape, [int]$effectIds[[string]$spec.effect], $msoAnimateLevelNone, [int]$triggerIds[[string]$spec.trigger])
                $effect.Timing.Duration = [double]$spec.duration
                $effect.Timing.TriggerDelayTime = [double]$spec.delay
                if ($null -ne $spec.repeat) { $effect.Timing.RepeatCount = [double]$spec.repeat }
                if ([bool]$spec.autoreverse) { $effect.Timing.AutoReverse = $msoTrue }
                if ([string]$spec.effect -eq "appear-exit" -or [bool]$spec.exit) { $effect.Exit = $msoTrue }
                if ([string]$spec.effect -eq "wipe") { $effect.EffectParameters.Direction = 4 }
                if ([string]$spec.effect -eq "motion-right") {
                    $motion = $effect.Behaviors.Item(1).MotionEffect
                    $motion.ByX = if ($null -ne $spec.motion_by_x) { [double]$spec.motion_by_x } else { 0.55 }
                    Release-ComObject $motion
                }
                if ([string]$spec.effect -eq "growShrink") {
                    $scale = $effect.Behaviors.Item(1).ScaleEffect
                    $amount = if ($null -ne $spec.scale_percent) { [double]$spec.scale_percent } else { 112.0 }
                    $scale.ByX = $amount
                    $scale.ByY = $amount
                    Release-ComObject $scale
                }
                $script:report.effects += [ordered]@{
                    slide = [int]$spec.slide
                    shape = [string]$spec.shape
                    effect = [string]$spec.effect
                    trigger = [string]$spec.trigger
                    duration = [double]$spec.duration
                    delay = [double]$spec.delay
                    repeat = if ($null -ne $spec.repeat) { [double]$spec.repeat } else { $null }
                    autoreverse = [bool]$spec.autoreverse
                    exit = ([int]$effect.Exit -eq $msoTrue)
                }
            } finally {
                Release-ComObject $effect
                Release-ComObject $sequence
                Release-ComObject $shape
                Release-ComObject $slide
            }
        }
    } | Out-Null
    Invoke-NativeStage -Name "apply_transitions" -Action {
        foreach ($spec in @($contract.transitions)) {
            $slide = $script:presentation.Slides.Item([int]$spec.slide)
            $transition = $null
            try {
                if ([string]$spec.type -ne "morph") { throw "Unsupported transition '$($spec.type)'." }
                $transition = $slide.SlideShowTransition
                $transition.EntryEffect = $ppEffectMorphByObject
                $transition.Duration = [double]$spec.duration
                $transition.AdvanceOnClick = if ([bool]$spec.advance_on_click) { $msoTrue } else { $msoFalse }
                if ($null -ne $spec.advance_after_seconds) {
                    $transition.AdvanceOnTime = $msoTrue
                    $transition.AdvanceTime = [double]$spec.advance_after_seconds
                } else {
                    $transition.AdvanceOnTime = $msoFalse
                }
                $script:report.transitions += [ordered]@{
                    slide = [int]$spec.slide
                    type = "morph"
                    duration = [double]$spec.duration
                    entry_effect = [int]$transition.EntryEffect
                }
            } finally {
                Release-ComObject $transition
                Release-ComObject $slide
            }
        }
    } | Out-Null
    Invoke-NativeStage -Name "save_reopen" -Action {
        $script:presentation.Save()
        $expectedSlides = [int]$script:presentation.Slides.Count
        Close-CurrentPresentation
        $script:presentation = $script:powerPoint.Presentations.Open($script:workPath, $msoFalse, $msoTrue, $msoFalse)
        if ([int]$script:presentation.Slides.Count -ne $expectedSlides) {
            throw "Slide count changed after save/reopen: $expectedSlides -> $($script:presentation.Slides.Count)."
        }
        Close-CurrentPresentation
    } | Out-Null
    Invoke-NativeStage -Name "create_video" -Action {
        foreach ($preview in @($contract.previews)) {
            $script:report.previews += New-AnimationPreview -SourcePptx $script:workPath -Preview $preview
        }
    } | Out-Null
    Invoke-NativeStage -Name "publish" -Action {
        Move-Item -LiteralPath $script:workPath -Destination $outputPath -Force
        $script:workPath = $null
        $script:report.output = $outputPath
    } | Out-Null
}

function Invoke-ProbeMode {
    if (-not $BaselinePptx -or -not $CandidatePptx -or -not $ProbeOutputDir) {
        throw "Probe mode requires BaselinePptx, CandidatePptx, and ProbeOutputDir."
    }
    $baselinePath = (Resolve-Path -LiteralPath $BaselinePptx).Path
    $candidatePath = (Resolve-Path -LiteralPath $CandidatePptx).Path
    $probeRoot = [IO.Path]::GetFullPath($ProbeOutputDir)
    New-Item -ItemType Directory -Force -Path $probeRoot | Out-Null
    $roundtripPath = Join-Path $probeRoot "roundtrip.pptx"
    $slidesPath = Join-Path $probeRoot "slides"

    Invoke-NativeStage -Name "startup" -Action {
        $script:powerPoint = New-Object -ComObject PowerPoint.Application
    } | Out-Null
    Invoke-NativeStage -Name "baseline_open" -Action {
        $baseline = $null
        try {
            $baseline = $script:powerPoint.Presentations.Open($baselinePath, $msoFalse, $msoTrue, $msoFalse)
            $script:report.artifacts.baseline_slide_count = [int]$baseline.Slides.Count
        } finally {
            if ($baseline) { try { $baseline.Close() } catch {}; Release-ComObject $baseline }
        }
    } | Out-Null
    Invoke-NativeStage -Name "candidate_open" -Action {
        $script:presentation = $script:powerPoint.Presentations.Open($candidatePath, $msoFalse, $msoTrue, $msoFalse)
        $script:report.slide_count = [int]$script:presentation.Slides.Count
    } | Out-Null
    Invoke-NativeStage -Name "save_reopen" -Action {
        Remove-Item -LiteralPath $roundtripPath -Force -ErrorAction SilentlyContinue
        $script:presentation.SaveCopyAs($roundtripPath, $ppSaveAsOpenXMLPresentation)
        $expectedSlides = [int]$script:presentation.Slides.Count
        Close-CurrentPresentation
        $script:presentation = $script:powerPoint.Presentations.Open($roundtripPath, $msoFalse, $msoTrue, $msoFalse)
        if ([int]$script:presentation.Slides.Count -ne $expectedSlides) {
            throw "Slide count changed after save/reopen: $expectedSlides -> $($script:presentation.Slides.Count)."
        }
        $script:report.artifacts.roundtrip = $roundtripPath
    } | Out-Null
    Invoke-NativeStage -Name "export" -Action {
        Remove-Item -LiteralPath $slidesPath -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $slidesPath | Out-Null
        $script:presentation.Export($slidesPath, "PNG", 1920, 1080)
        $exportedSlides = @(Get-ChildItem -LiteralPath $slidesPath -Filter "*.png" -File).Count
        if ($exportedSlides -ne [int]$script:report.slide_count) {
            throw "Export produced $exportedSlides/$($script:report.slide_count) slide images."
        }
        $script:report.artifacts.slides = $slidesPath
    } | Out-Null
}

try {
    if ($Mode -eq "Apply") { Invoke-ApplyMode } else { Invoke-ProbeMode }
    $script:report.status = "passed"
    $script:report.classification = "passed"
} catch {
    $script:report.status = "failed"
    $script:report.failed_stage = $script:currentStage
    if ($Mode -eq "Probe") {
        $script:report.classification = switch ($script:currentStage) {
            "startup" { "office_environment"; break }
            "baseline_open" { "office_environment"; break }
            "candidate_open" { "candidate_compatibility"; break }
            "save_reopen" { "candidate_roundtrip"; break }
            "export" { "office_export"; break }
            default { "input_or_process" }
        }
    } else {
        $script:report.classification = switch ($script:currentStage) {
            "startup" { "office_environment"; break }
            "candidate_open" { "candidate_compatibility"; break }
            "save_reopen" { "candidate_roundtrip"; break }
            "create_video" { "office_video_export"; break }
            default { "native_apply" }
        }
    }
    $hresult = $null
    if ($null -ne $_.Exception.HResult) {
        $unsigned = [BitConverter]::ToUInt32([BitConverter]::GetBytes([int]$_.Exception.HResult), 0)
        $hresult = "0x{0:X8}" -f $unsigned
    }
    $script:report.error = [ordered]@{
        type = $_.Exception.GetType().FullName
        message = $_.Exception.Message
        hresult = $hresult
    }
} finally {
    Close-CurrentPresentation
    if ($script:powerPoint) {
        try { $script:powerPoint.Quit() } catch {}
        Release-ComObject $script:powerPoint
        $script:powerPoint = $null
    }
    if ($script:workPath) { Remove-Item -LiteralPath $script:workPath -Force -ErrorAction SilentlyContinue }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

$script:report | ConvertTo-Json -Depth 12 -Compress
if ($script:report.status -ne "passed") { exit 1 }
