#Requires -Version 5.1
<#
.SYNOPSIS
  Install a private 64-bit CPython next to Daedalus (no PATH, no Administrator).

Uses the official python.org NuGet package (PSF), the documented way to
redistribute CPython with an application. Not the embeddable zip (no venv/Tk).
#>

$script:DaedalusPythonVersion = "3.12.10"
$script:DaedalusPythonNupkg = "python.$($script:DaedalusPythonVersion).nupkg"
$script:DaedalusPythonUrl = "https://globalcdn.nuget.org/packages/$($script:DaedalusPythonNupkg)"
# SHA-512 of the nupkg as published on nuget.org (packageHash, Base64).
$script:DaedalusPythonSha512 = "u9pNz2iKlCEbYtUJaKkbOPMF0LjR7NkCafdKhvigpPzrt8oWKgdTpHaR6z3wyWQAm9PYGUxv0Zr66NX9AeHMDw=="

function Get-FileSha512Base64([string] $Path) {
    $sha = [System.Security.Cryptography.SHA512]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            return [Convert]::ToBase64String($sha.ComputeHash($stream))
        }
        finally { $stream.Dispose() }
    }
    finally { $sha.Dispose() }
}

function Test-DaedalusPythonExe([string] $PythonExe) {
    if (-not (Test-Path $PythonExe)) { return $false }
    & $PythonExe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Install-DaedalusBundledPython {
    param(
        [Parameter(Mandatory = $true)][string] $RuntimeRoot,
        [string] $RedistDir = ""
    )

    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "The bundled CPython is 64-bit. Use a 64-bit Windows install, or install Python 3.11+ yourself."
    }

    $pythonExe = Join-Path $RuntimeRoot "python.exe"
    if (Test-DaedalusPythonExe $pythonExe) {
        Write-Host "Using existing bundled Python at $pythonExe"
        return $pythonExe
    }

    if (-not $RedistDir) {
        $RedistDir = Join-Path $PSScriptRoot "redist"
    }
    New-Item -ItemType Directory -Force -Path $RedistDir | Out-Null
    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

    $nupkg = Join-Path $RedistDir $script:DaedalusPythonNupkg
    if (-not (Test-Path $nupkg)) {
        Write-Host "Downloading CPython $($script:DaedalusPythonVersion) (NuGet python package, ~14 MB)..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest -Uri $script:DaedalusPythonUrl -OutFile $nupkg -UseBasicParsing -UserAgent "Daedalus-installer"
    }

    $got = Get-FileSha512Base64 $nupkg
    if ($got -ne $script:DaedalusPythonSha512) {
        Remove-Item -Force $nupkg -ErrorAction SilentlyContinue
        throw "Python nupkg checksum mismatch (got $got). Delete $nupkg and retry."
    }

    $zip = Join-Path $RedistDir ("python." + $script:DaedalusPythonVersion + ".zip")
    Copy-Item -Force $nupkg $zip
    $unpack = Join-Path $RedistDir ("unpack-" + $script:DaedalusPythonVersion)
    if (Test-Path $unpack) { Remove-Item -Recurse -Force $unpack }
    Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force
    Remove-Item -Force $zip -ErrorAction SilentlyContinue

    $tools = Join-Path $unpack "tools"
    if (-not (Test-Path (Join-Path $tools "python.exe"))) {
        throw "NuGet package did not contain tools\python.exe"
    }

    # Replace runtime atomically-ish: copy tools/* into runtime/.
    Get-ChildItem -Force $RuntimeRoot -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-Item -Recurse -Force $_.FullName
    }
    Copy-Item -Recurse -Force (Join-Path $tools "*") $RuntimeRoot
    Remove-Item -Recurse -Force $unpack -ErrorAction SilentlyContinue

    if (-not (Test-DaedalusPythonExe $pythonExe)) {
        throw "Bundled Python did not run after extract: $pythonExe"
    }
    Write-Host "Bundled CPython $($script:DaedalusPythonVersion) -> $pythonExe"
    return $pythonExe
}
