[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$repoRoot = Split-Path $projectRoot -Parent
. (Join-Path $repoRoot 'scripts/studio-runtime.ps1')
$node = Get-StudioNode $repoRoot
$runtime = Join-Path $projectRoot '.tools/media-inspect-runtime'
if ($IsWindows) {
    $npm = Join-Path (Split-Path $node -Parent) 'npm.cmd'
    if (-not (Test-Path -LiteralPath $npm -PathType Leaf)) { throw "npm.cmd paired with the selected Node runtime is missing: $npm" }
    & $npm install --prefix $runtime --save-exact --ignore-scripts --no-audit --no-fund 'mediabunny@1.55.7'
}
else {
    $npm = Join-Path (Split-Path $node -Parent) 'npm'
    if (-not (Test-Path -LiteralPath $npm -PathType Leaf)) { throw "npm paired with the selected Node runtime is missing: $npm" }
    $npmCli = (Resolve-Path -LiteralPath $npm).Path
    & $node $npmCli install --prefix $runtime --save-exact --ignore-scripts --no-audit --no-fund 'mediabunny@1.55.7'
}
exit $LASTEXITCODE
