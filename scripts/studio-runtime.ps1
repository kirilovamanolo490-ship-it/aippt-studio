# Shared platform-aware Python selection; never choose a copied Windows exe on Unix.
function Get-StudioPhysicalPath([string]$Path) {
    # ResolveLinkTarget follows a symlink chain on PowerShell 7.3+.  Resolve-Path
    # intentionally preserves a symlink's written name on macOS, so it cannot
    # prove that an innocuous-looking override does not actually point into an
    # old Work or legacy job directory.
    $full = [IO.Path]::GetFullPath($Path)
    try {
        $target = [IO.FileInfo]::new($full).ResolveLinkTarget($true)
        if ($null -ne $target) { return $target.FullName }
    }
    catch {
        # The ordinary path checks below will produce the actionable error for
        # a missing, unreadable, or cyclic link.
    }
    return $full
}

function ConvertTo-StudioPythonArchitecture([string]$Architecture) {
    # ``platform.machine()`` describes the Python process, while
    # RuntimeInformation.OSArchitecture describes the physical host.  Keep the
    # names in the same small vocabulary used by the Node selector so a
    # Rosetta x64 Python cannot pass as a native Apple-Silicon runtime.
    if ([string]::IsNullOrWhiteSpace($Architecture)) { return "" }
    switch ($Architecture.Trim().ToLowerInvariant()) {
        "x86_64" { return "x64" }
        "amd64" { return "x64" }
        "aarch64" { return "arm64" }
        "i386" { return "x86" }
        "i686" { return "x86" }
        default { return $Architecture.Trim().ToLowerInvariant() }
    }
}

function Get-StudioPythonHostIdentity {
    $onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    $platform = if ($onWindows) { "win32" } elseif ($IsMacOS) { "darwin" } else { "linux" }
    $architecture = ConvertTo-StudioPythonArchitecture ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString())
    return @{ platform = $platform; architecture = $architecture }
}

function Get-StudioPython([string]$RepoRoot) {
    $onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    $hostIdentity = Get-StudioPythonHostIdentity
    $candidates = @()
    if ($env:AIPPT_PYTHON) { $candidates = @($env:AIPPT_PYTHON) }
    elseif ($onWindows) { $candidates = @((Join-Path $RepoRoot "aippt-studio/.venv/Scripts/python.exe")) }
    elseif ($IsMacOS) {
        $candidates = @((Join-Path $RepoRoot "aippt-studio/.venv-macos/bin/python"), (Join-Path $RepoRoot "aippt-studio/.venv/bin/python"))
    }
    else {
        $candidates = @((Join-Path $RepoRoot "aippt-studio/.venv/bin/python"))
    }
    $diagnostics = [System.Collections.Generic.List[string]]::new()
    foreach ($candidate in $candidates) {
        $resolved = $candidate
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            $command = Get-Command -Name $candidate -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command) { $resolved = $command.Source }
        }
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            $diagnostics.Add("${candidate}: not found")
            continue
        }
        $full = [IO.Path]::GetFullPath($resolved)
        $physical = Get-StudioPhysicalPath $full
        if (-not $onWindows -and [IO.Path]::GetExtension($full) -eq ".exe") {
            $diagnostics.Add("${candidate}: Windows executable cannot run on this host")
            continue
        }
        if ($physical -match '[/\\]work[/\\]\d+[/\\]' -or $physical -match '[/\\]jobs[/\\]') {
            $diagnostics.Add("${candidate}: historical Work environments are not Studio runtimes")
            continue
        }
        try {
            $stream = [IO.File]::OpenRead($physical)
            try { $first = $stream.ReadByte(); $second = $stream.ReadByte() }
            finally { $stream.Dispose() }
            if (-not $onWindows -and $first -eq 77 -and $second -eq 90) {
                $diagnostics.Add("${candidate}: PE/Windows binary cannot execute on this host")
                continue
            }
            # A copied or broken venv can exist on disk but fail to execute. More
            # subtly, an x64 Python can execute under Rosetta on an Apple-Silicon
            # Mac. Probe the process identity before returning it; preserve
            # ``$full`` as the launcher rather than resolving the venv symlink,
            # because resolution can skip pyvenv.cfg and run the base interpreter.
            $probe = @(& $full -c 'import json,platform,sys; print(json.dumps({"platform":sys.platform,"architecture":platform.machine(),"version":"%s.%s.%s" % sys.version_info[:3]}))' 2>&1)
            if ($LASTEXITCODE -ne 0) {
                $diagnostics.Add("${candidate}: Python identity probe failed (exit $LASTEXITCODE): $($probe -join ' ')")
                continue
            }
            try {
                $identity = ($probe[-1] | ConvertFrom-Json)
                $pythonArchitecture = ConvertTo-StudioPythonArchitecture ([string]$identity.architecture)
                if ($identity.platform -ne $hostIdentity.platform -or $pythonArchitecture -ne $hostIdentity.architecture) {
                    $remedy = if ($hostIdentity.platform -eq "darwin" -and $hostIdentity.architecture -eq "arm64" -and $pythonArchitecture -eq "x64") {
                        " Use an arm64 Python 3.11+ environment instead of a Rosetta x64 interpreter."
                    } else {
                        " Use a Python 3.11+ executable built for this host architecture."
                    }
                    $diagnostics.Add("${candidate}: Python identity $($identity.platform)/$pythonArchitecture does not match host $($hostIdentity.platform)/$($hostIdentity.architecture).$remedy")
                    continue
                }
                if ([string]$identity.version -notmatch '^(\d+)\.(\d+)(?:\.\d+)?$') {
                    $diagnostics.Add("${candidate}: Python identity version is invalid")
                    continue
                }
                $major, $minor = [int]$Matches[1], [int]$Matches[2]
                if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
                    $diagnostics.Add("${candidate}: Python $($identity.version) is below the required 3.11")
                    continue
                }
                return $full
            }
            catch {
                $diagnostics.Add("${candidate}: Python identity probe returned invalid JSON: $($probe -join ' ')")
            }
        }
        catch {
            $diagnostics.Add("${candidate}: $($_.Exception.Message)")
        }
    }
    throw "No compatible Studio Python environment. Run the platform setup in aippt-studio/README.md or set AIPPT_PYTHON to a Python 3.11+ executable. $($diagnostics -join '; ')"
}

function Get-StudioNode([string]$RepoRoot) {
    # Setup utilities are part of the shared runtime, not a chance to borrow a
    # Node binary from an old Work.  Verify the process identity as well as its
    # file presence so a copied Windows executable or Rosetta/foreign slice is
    # rejected before npm or a native addon gets a chance to load it.
    $onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    $expectedPlatform = if ($onWindows) { "win32" } elseif ($IsMacOS) { "darwin" } else { "linux" }
    $osArchitecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
    $expectedArchitecture = switch ($osArchitecture) {
        "arm64" { "arm64" }
        "x64" { "x64" }
        "x86" { "ia32" }
        default { $osArchitecture }
    }
    $candidates = if ($env:AIPPT_NODE) { @($env:AIPPT_NODE) } else { @("node") }
    $diagnostics = [System.Collections.Generic.List[string]]::new()
    foreach ($candidate in $candidates) {
        $resolved = $candidate
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            $command = Get-Command -Name $candidate -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command) { $resolved = $command.Source }
        }
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            $diagnostics.Add("${candidate}: not found")
            continue
        }
        $full = [IO.Path]::GetFullPath($resolved)
        $physical = Get-StudioPhysicalPath $full
        if ($physical -match '[/\\]work[/\\]\d+[/\\]' -or $physical -match '[/\\]jobs[/\\]') {
            $diagnostics.Add("${candidate}: historical Work or legacy job runtime is not allowed")
            continue
        }
        try {
            $stream = [IO.File]::OpenRead($physical)
            try { $first = $stream.ReadByte(); $second = $stream.ReadByte() }
            finally { $stream.Dispose() }
            if (-not $onWindows -and $first -eq 77 -and $second -eq 90) {
                $diagnostics.Add("${candidate}: Windows executable cannot run on this host")
                continue
            }
            $probe = @(& $full -p 'JSON.stringify({platform:process.platform,architecture:process.arch,version:process.version,executable:process.execPath})' 2>&1)
            if ($LASTEXITCODE -ne 0) {
                $diagnostics.Add("${candidate}: Node identity probe failed (exit $LASTEXITCODE): $($probe -join ' ')")
                continue
            }
            $identity = ($probe[-1] | ConvertFrom-Json)
            if ($identity.platform -ne $expectedPlatform -or $identity.architecture -ne $expectedArchitecture) {
                $diagnostics.Add("${candidate}: Node identity $($identity.platform)/$($identity.architecture) does not match $expectedPlatform/$expectedArchitecture")
                continue
            }
            if ([string]$identity.version -notmatch '^v?(\d+)\.(\d+)') {
                $diagnostics.Add("${candidate}: Node version is invalid")
                continue
            }
            $major, $minor = [int]$Matches[1], [int]$Matches[2]
            if ($major -lt 22 -or ($major -eq 22 -and $minor -lt 12)) {
                $diagnostics.Add("${candidate}: Node $($identity.version) is below the required 22.12")
                continue
            }
            return $full
        }
        catch {
            $diagnostics.Add("${candidate}: $($_.Exception.Message)")
        }
    }
    throw "No compatible host-native Node.js 22.12+ runtime. Set AIPPT_NODE to a current host executable. $($diagnostics -join '; ')"
}

function Get-StudioMacBinaryArchitectures([string]$Path) {
    # `uv --version` describes its release but not the native slice that
    # launched under Rosetta.  Ask the host-owned file utility about the
    # resolved binary, never a PATH shadow.  The macOS output covers both a
    # thin Mach-O (`arm64`, `x86_64`) and universal binaries.
    $fileTool = "/usr/bin/file"
    if (-not (Test-Path -LiteralPath $fileTool -PathType Leaf)) { return @() }
    try {
        $description = @(& $fileTool -b -- $Path 2>&1)
        if ($LASTEXITCODE -ne 0) { return @() }
    }
    catch { return @() }
    $text = $description -join " "
    if ($text -notmatch '(?i)Mach-O') { return @() }
    $architectures = [System.Collections.Generic.List[string]]::new()
    if ($text -match '(?i)\barm64e?\b') { $architectures.Add("arm64") }
    if ($text -match '(?i)\b(?:x86_64|x86-64)\b') { $architectures.Add("x64") }
    if ($text -match '(?i)\b(?:i386|x86)\b') { $architectures.Add("x86") }
    return @($architectures | Select-Object -Unique)
}

function Get-StudioUv([string]$RepoRoot) {
    # The media installer has no reason to execute a copied executable from a
    # retired Work.  uv is intentionally host-managed, but its identity is
    # still checked before it is allowed to change the isolated media runtime.
    $onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    $hostIdentity = Get-StudioPythonHostIdentity
    $candidates = if ($env:AIPPT_UV) { @($env:AIPPT_UV) } else { @("uv") }
    $diagnostics = [System.Collections.Generic.List[string]]::new()
    foreach ($candidate in $candidates) {
        $resolved = $candidate
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            $command = Get-Command -Name $candidate -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command) { $resolved = $command.Source }
        }
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            $diagnostics.Add("${candidate}: not found")
            continue
        }
        $full = [IO.Path]::GetFullPath($resolved)
        $physical = Get-StudioPhysicalPath $full
        if ($physical -match '[/\\]work[/\\]\d+[/\\]' -or $physical -match '[/\\]jobs[/\\]') {
            $diagnostics.Add("${candidate}: historical Work or legacy job runtime is not allowed")
            continue
        }
        try {
            $stream = [IO.File]::OpenRead($physical)
            try { $first = $stream.ReadByte(); $second = $stream.ReadByte() }
            finally { $stream.Dispose() }
            if (-not $onWindows -and $first -eq 77 -and $second -eq 90) {
                $diagnostics.Add("${candidate}: Windows executable cannot run on this host")
                continue
            }
            if ($hostIdentity.platform -eq "darwin") {
                $architectures = @(Get-StudioMacBinaryArchitectures $physical)
                if ($architectures.Count -eq 0) {
                    $diagnostics.Add("${candidate}: macOS uv must be a readable host-native Mach-O executable")
                    continue
                }
                if ($architectures -notcontains $hostIdentity.architecture) {
                    $diagnostics.Add("${candidate}: uv native architectures $($architectures -join ',') do not match host $($hostIdentity.platform)/$($hostIdentity.architecture)")
                    continue
                }
            }
            $probe = @(& $full --version 2>&1)
            if ($LASTEXITCODE -eq 0 -and $probe.Count -gt 0) { return $full }
            $diagnostics.Add("${candidate}: uv version probe failed (exit $LASTEXITCODE): $($probe -join ' ')")
        }
        catch {
            $diagnostics.Add("${candidate}: $($_.Exception.Message)")
        }
    }
    throw "No compatible host uv executable. Set AIPPT_UV to a current host binary. $($diagnostics -join '; ')"
}

function Get-StudioRuntimeEnvironment([string]$Python) {
    if (-not $env:AIPPT_JOBS_DIR) { return @{} }
    $workspace = Split-Path $env:AIPPT_JOBS_DIR -Parent
    $report = Join-Path $workspace "runtime-environment.json"
    if (-not (Test-Path -LiteralPath $report -PathType Leaf)) { return @{} }
    try {
        $validated = @(& $Python -m aippt_studio.platform_runtime validate-environment --report $report 2>&1)
        if ($LASTEXITCODE -ne 0) { throw ($validated -join ' ') }
        return ($validated -join "`n" | ConvertFrom-Json -AsHashtable)
    }
    catch {
        # Optional Artifact Tool trouble must not stop the native Studio route.
        [Console]::Error.WriteLine("Saved Work runtime was not loaded: $($_.Exception.Message). Run runtime selection again; core Studio remains available.")
        return @{}
    }
}
