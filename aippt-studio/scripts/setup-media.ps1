[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$repoRoot = Split-Path $projectRoot -Parent
. (Join-Path $repoRoot 'scripts/studio-runtime.ps1')
$python = Get-StudioPython $repoRoot
$uv = Get-StudioUv $repoRoot
& $uv pip install --python $python --target (Join-Path $projectRoot '.tools/media-runtime') 'imageio-ffmpeg==0.6.0'
exit $LASTEXITCODE
