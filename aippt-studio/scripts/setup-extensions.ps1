[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$repoRoot = Split-Path $projectRoot -Parent
. (Join-Path $repoRoot 'scripts/studio-runtime.ps1')
$node = Get-StudioNode $repoRoot
$runtime = Join-Path $projectRoot '.tools/extension-runtime'
$manifests = Join-Path $projectRoot 'extensions'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $manifests 'package.json') -Destination $runtime -Force
Copy-Item -LiteralPath (Join-Path $manifests 'package-lock.json') -Destination $runtime -Force
if ($IsWindows) {
    $npm = Join-Path (Split-Path $node -Parent) 'npm.cmd'
    if (-not (Test-Path -LiteralPath $npm -PathType Leaf)) { throw "npm.cmd paired with the selected Node runtime is missing: $npm" }
    & $npm ci --prefix $runtime --ignore-scripts
}
else {
    $npm = Join-Path (Split-Path $node -Parent) 'npm'
    if (-not (Test-Path -LiteralPath $npm -PathType Leaf)) { throw "npm paired with the selected Node runtime is missing: $npm" }
    $npmCli = (Resolve-Path -LiteralPath $npm).Path
    & $node $npmCli ci --prefix $runtime --ignore-scripts
}
exit $LASTEXITCODE
