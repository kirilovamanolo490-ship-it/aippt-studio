param(
    [Parameter(Mandatory = $true)] [string] $Pptx,
    [Parameter(Mandatory = $true)] [string] $OutputDir,
    [string] $ReferencePptx = ""
)

$ErrorActionPreference = "Stop"
$pptxPath = [IO.Path]::GetFullPath($Pptx)
$outputPath = [IO.Path]::GetFullPath($OutputDir)
$referencePath = if ($ReferencePptx) { [IO.Path]::GetFullPath($ReferencePptx) } else { $null }
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
$slidesPath = Join-Path $outputPath "slides"
New-Item -ItemType Directory -Force -Path $slidesPath | Out-Null

$powerPoint = $null
$presentation = $null
$reference = $null
$exitCode = 0
$phase = "initialize"
$report = [ordered]@{
    status = "failed"
    phase = $phase
    classification = "unknown"
    hresult = $null
    message = $null
    reference = $referencePath
    slide_count = $null
    slides = $null
}

try {
    $phase = "com_start"
    $powerPoint = New-Object -ComObject PowerPoint.Application

    if ($referencePath) {
        $phase = "reference_open"
        $reference = $powerPoint.Presentations.Open($referencePath, $true, $true, $false)
        $reference.Close()
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($reference)
        $reference = $null
    }

    $phase = "candidate_open"
    $presentation = $powerPoint.Presentations.Open($pptxPath, $false, $false, $false)
    $slideCount = $presentation.Slides.Count

    $phase = "candidate_save"
    $presentation.Save()

    $phase = "png_export"
    Get-ChildItem -LiteralPath $slidesPath -File | Remove-Item -Force
    $presentation.Export($slidesPath, "PNG", 1920, 1080)
    $exportedSlides = @(Get-ChildItem -LiteralPath $slidesPath -File |
        Where-Object { $_.Extension -ieq ".png" } |
        ForEach-Object {
            if ($_.BaseName -notmatch "^slide[-_ ]?(\d+)$") {
                throw "PowerPoint exported an unexpected slide image name: $($_.Name)"
            }
            [pscustomobject]@{ Page = [int]$Matches[1]; File = $_ }
        } |
        Sort-Object Page)
    if ($exportedSlides.Count -ne $slideCount) {
        throw "PowerPoint exported $($exportedSlides.Count) PNG files for $slideCount slides"
    }
    for ($index = 0; $index -lt $exportedSlides.Count; $index++) {
        $slide = $exportedSlides[$index]
        $expectedPage = $index + 1
        if ($slide.Page -ne $expectedPage) {
            throw "PowerPoint exported duplicate or missing slide image page $expectedPage"
        }
        $expectedPath = Join-Path $slidesPath "slide-$expectedPage.png"
        if ($slide.File.FullName -ne $expectedPath) {
            Move-Item -LiteralPath $slide.File.FullName -Destination $expectedPath -Force
        }
    }

    $phase = "candidate_close"
    $presentation.Close()
    [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($presentation)
    $presentation = $null

    $phase = "candidate_reopen"
    $presentation = $powerPoint.Presentations.Open($pptxPath, $false, $true, $false)
    $reopenedCount = $presentation.Slides.Count
    if ($slideCount -ne $reopenedCount) {
        throw "Slide count changed after roundtrip: $slideCount -> $reopenedCount"
    }
    $presentation.Close()
    [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($presentation)
    $presentation = $null

    $report.status = "passed"
    $report.phase = "complete"
    $report.classification = "none"
    $report.slide_count = $slideCount
    $report.slides = $slidesPath
}
catch {
    $exitCode = 1
    $exception = $_.Exception
    $report.phase = $phase
    $report.message = $exception.Message
    $report.hresult = "0x{0:X8}" -f ([int64]$exception.HResult -band 0xffffffff)
    if ($phase -in @("com_start", "reference_open")) {
        $report.classification = "environment"
    }
    elseif ($phase -in @("candidate_open", "candidate_save", "candidate_close", "candidate_reopen")) {
        $report.classification = "candidate"
    }
    elseif ($phase -eq "png_export") {
        $report.classification = "export"
    }
}
finally {
    if ($reference) {
        try { $reference.Close() } catch {}
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($reference)
    }
    if ($presentation) {
        try { $presentation.Close() } catch {}
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($presentation)
    }
    if ($powerPoint) {
        try { $powerPoint.Quit() } catch {}
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($powerPoint)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

$report | ConvertTo-Json -Compress -Depth 6
exit $exitCode
