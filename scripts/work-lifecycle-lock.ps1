Set-StrictMode -Version Latest

function Open-WorkLifecycleRecord {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RecordPath,
        [ValidateRange(1, 300)]
        [int]$TimeoutSeconds = 30
    )

    # Opening work.json for ReadWrite while sharing only reads is the common
    # lifecycle lease. On macOS/Linux it conflicts with the Python fcntl record
    # lock used by packet-import; on Windows it also prevents another writer
    # from opening the record. We intentionally lock the existing record, not
    # a new sidecar, so a sealed Work is never touched merely to create a lock.
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ($true) {
        try {
            return [IO.File]::Open(
                $RecordPath,
                [IO.FileMode]::Open,
                [IO.FileAccess]::ReadWrite,
                [IO.FileShare]::Read
            )
        }
        catch [IO.IOException] {
            if ([DateTime]::UtcNow -ge $deadline) {
                throw "Timed out waiting for the Work lifecycle lock: $RecordPath"
            }
            Start-Sleep -Milliseconds 100
        }
    }
}

function Read-WorkLifecycleRecord {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][IO.FileStream]$Stream)

    $Stream.Position = 0
    $reader = [IO.StreamReader]::new($Stream, [Text.UTF8Encoding]::new($false), $true, 4096, $true)
    try {
        return $reader.ReadToEnd()
    }
    finally {
        $reader.Dispose()
    }
}

function Write-WorkLifecycleRecord {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][IO.FileStream]$Stream,
        [Parameter(Mandatory = $true)][string]$Text
    )

    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Text + [Environment]::NewLine)
    $Stream.Position = 0
    $Stream.SetLength(0)
    $Stream.Write($bytes, 0, $bytes.Length)
    $Stream.Flush($true)
}

function Close-WorkLifecycleRecord {
    [CmdletBinding()]
    param([AllowNull()][IO.FileStream]$Stream)

    if ($null -ne $Stream) {
        $Stream.Dispose()
    }
}
