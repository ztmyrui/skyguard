param(
    [Parameter(Mandatory)] [string] $Python,
    [Parameter(Mandatory)] [string] $DistPath,
    [string] $WorkPath = (Join-Path $env:TEMP 'skyguard-build')
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

if (Test-Path -LiteralPath $Python -PathType Leaf) {
    $Python = (Resolve-Path -LiteralPath $Python).Path
}
$DistPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($DistPath)
$WorkPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($WorkPath)

Push-Location -LiteralPath $projectRoot
try {
    # PyInstaller logs INFO lines to stderr; under 'Stop' Windows PowerShell 5.1 would
    # turn the first one into a terminating NativeCommandError. Judge by exit code instead.
    $ErrorActionPreference = 'Continue'
    & $Python -m PyInstaller --noconfirm --clean --distpath $DistPath --workpath $WorkPath Skyguard.spec 2>&1 |
        ForEach-Object { "$_" }
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop build failed (exit code $LASTEXITCODE)"
    }
} finally {
    Pop-Location
}
