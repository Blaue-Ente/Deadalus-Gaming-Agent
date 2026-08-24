#Requires -Version 5.1
<#
.SYNOPSIS
  Build dist\Daedalus-Setup.exe on a Windows machine (Inno Setup 6).

Prefetches the official CPython NuGet package into packaging\windows\redist
so the installer does not need to download Python at install time.
pip packages (FastAPI, Chroma, …) are still fetched on first install.
#>
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = (Resolve-Path (Join-Path $here "..\..")).Path
Set-Location $root

. (Join-Path $here "Get-BundledPython.ps1")

$redist = Join-Path $here "redist"
New-Item -ItemType Directory -Force -Path $redist | Out-Null
$nupkg = Join-Path $redist $script:DaedalusPythonNupkg
if (-not (Test-Path $nupkg)) {
    Write-Host "Fetching $script:DaedalusPythonNupkg for the installer payload…"
    $tmpRuntime = Join-Path $env:TEMP "daedalus-python-prefetch"
    if (Test-Path $tmpRuntime) { Remove-Item -Recurse -Force $tmpRuntime }
    # Download + verify via the same helper; then keep only the nupkg in redist.
    Install-DaedalusBundledPython -RuntimeRoot $tmpRuntime -RedistDir $redist | Out-Null
    Remove-Item -Recurse -Force $tmpRuntime -ErrorAction SilentlyContinue
}
if (-not (Test-Path $nupkg)) {
    throw "Expected $nupkg after prefetch"
}

$isccCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
)
$iscc = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) {
    Write-Host "Inno Setup 6 not found. Install from https://jrsoftware.org/isinfo.php then re-run." -ForegroundColor Yellow
    Write-Host "Python nupkg is ready at $nupkg — compile with: iscc packaging\windows\Daedalus.iss"
    exit 2
}

$iss = Join-Path $here "Daedalus.iss"
Write-Host "Compiling $iss with $iscc"
& $iscc $iss
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit $LASTEXITCODE" }
$out = Join-Path $root "dist\Daedalus-Setup.exe"
if (-not (Test-Path $out)) { throw "Expected output $out" }
Write-Host "Built $out"
