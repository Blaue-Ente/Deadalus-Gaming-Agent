#Requires -Version 5.1
<#
.SYNOPSIS
  Install Дедал as a per-user Windows app (Start Menu, no Administrator).

.DESCRIPTION
  Copies the product to %LOCALAPPDATA%\Programs\Daedalus, creates a venv,
  pip-installs, writes silent/console launchers, and a Start Menu shortcut.

  Data (memory, playground, .env) lives in %LOCALAPPDATA%\Daedalus — kept
  across reinstalls.

.PARAMETER InPlace
  Product files are already at the install location (used by Inno Setup).

.PARAMETER DesktopShortcut
  Also put a shortcut on the public desktop of this user.

.PARAMETER Startup
  Launch Дедал when Windows signs in.
#>
param(
    [switch] $InPlace,
    [switch] $DesktopShortcut,
    [switch] $Startup
)

$ErrorActionPreference = "Stop"
$AppName = "Daedalus"
$DisplayName = "Дедал"
$InstallRoot = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$DataRoot = Join-Path $env:LOCALAPPDATA $AppName
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Find-Python {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.12") },
        @{ Exe = "py"; Args = @("-3.11") },
        @{ Exe = "py"; Args = @("-3") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $probe = @($c.Args) + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)")
        & $c.Exe @probe 2>$null
        if ($LASTEXITCODE -eq 0) {
            return @{ Exe = $c.Exe; Args = @($c.Args) }
        }
    }
    return $null
}

function Invoke-RobocopyCopy([string] $From, [string] $To) {
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    $xd = @(".git", ".venv", "data", "playground", "__pycache__", ".pytest_cache", "daedalus.egg-info", ".agents")
    & robocopy $From $To /E /NFL /NDL /NJH /NJS /NP /XD @xd /XF "*.pyc" ".env" | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit $LASTEXITCODE"
    }
}

function Write-Launcher([string] $Root) {
    $cmdPath = Join-Path $Root "Daedalus.cmd"
    $vbsPath = Join-Path $Root "Daedalus.vbs"
    $pyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    @"
@echo off
setlocal
cd /d "%~dp0"
set DAEDALUS_STANDALONE=1
set DAEDALUS_WINDOWS_APP=1
set DAEDALUS_DATA=%LOCALAPPDATA%\Daedalus
set DAEDALUS_PLAYGROUND=%LOCALAPPDATA%\Daedalus\playground
set HOST=127.0.0.1
set PORT=8787
"$py" -m app.cli desktop
"@ | Set-Content -Encoding ASCII -Path $cmdPath
    $vbs = @"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$Root"
sh.Run """$pyw"" -m app.cli desktop", 0, False
"@
    $vbs | Set-Content -Encoding ASCII -Path $vbsPath
    $srcUn = Join-Path $PSScriptRoot "uninstall.ps1"
    $dstUn = Join-Path $Root "uninstall.ps1"
    $srcFull = (Resolve-Path $srcUn).Path
    $dstFull = ""
    if (Test-Path $dstUn) { $dstFull = (Resolve-Path $dstUn).Path }
    if ($srcFull -ne $dstFull) {
        Copy-Item -Force $srcUn $dstUn
    }
}

function New-DaedalusShortcut([string] $LinkPath, [string] $Target) {
    $dir = Split-Path -Parent $LinkPath
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $w = New-Object -ComObject WScript.Shell
    $s = $w.CreateShortcut($LinkPath)
    $s.TargetPath = $Target
    $s.WorkingDirectory = $InstallRoot
    $s.WindowStyle = 7
    $s.Description = "$DisplayName — coding agent (Studio)"
    $icon = Join-Path $InstallRoot "app\static\daedalus-mark.png"
    if (Test-Path $icon) { $s.IconLocation = $icon }
    $s.Save()
}

Write-Host "=== $DisplayName / $AppName — Windows install ==="
$py = Find-Python
if (-not $py) {
    Write-Host "Трябва Python 3.11+ (не Admin)." -ForegroundColor Yellow
    Write-Host "Свали от https://www.python.org/downloads/windows/ и маркирай 'Add python.exe to PATH'."
    exit 2
}

if ($InPlace) {
    $InstallRoot = $RepoRoot
}
else {
    Write-Host "Копирам в $InstallRoot"
    Invoke-RobocopyCopy -From $RepoRoot -To $InstallRoot
}

New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot "playground") | Out-Null
$envExample = Join-Path $InstallRoot ".env.example"
$envTarget = Join-Path $DataRoot ".env"
if ((Test-Path $envExample) -and -not (Test-Path $envTarget)) {
    Copy-Item $envExample $envTarget
    Write-Host "Създаден $envTarget — сложи API ключ или ползвай Ollama."
}

$venv = Join-Path $InstallRoot ".venv"
Write-Host "venv + pip install (първи път може да отнеме няколко минути)…"
$venvArgs = @($py.Args) + @("-m", "venv", $venv)
& $py.Exe @venvArgs
if ($LASTEXITCODE -ne 0) { throw "venv failed" }
$venvPy = Join-Path $venv "Scripts\python.exe"
& $venvPy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $venvPy -m pip install -e $InstallRoot
if ($LASTEXITCODE -ne 0) { throw "pip install daedalus failed" }

Write-Launcher -Root $InstallRoot

$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-DaedalusShortcut (Join-Path $startMenu "$DisplayName.lnk") (Join-Path $InstallRoot "Daedalus.vbs")
if ($DesktopShortcut) {
    New-DaedalusShortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "$DisplayName.lnk") (Join-Path $InstallRoot "Daedalus.vbs")
}
if ($Startup) {
    $startupDir = [Environment]::GetFolderPath("Startup")
    New-DaedalusShortcut (Join-Path $startupDir "$DisplayName.lnk") (Join-Path $InstallRoot "Daedalus.vbs")
}

$uninst = 'powershell.exe -ExecutionPolicy Bypass -File "' + (Join-Path $InstallRoot "uninstall.ps1") + '"'
$reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppName"
New-Item -Path $reg -Force | Out-Null
New-ItemProperty -Path $reg -Name "DisplayName" -Value $DisplayName -Force | Out-Null
New-ItemProperty -Path $reg -Name "DisplayVersion" -Value "0.3.2" -Force | Out-Null
New-ItemProperty -Path $reg -Name "Publisher" -Value "Blaue-Ente" -Force | Out-Null
New-ItemProperty -Path $reg -Name "InstallLocation" -Value $InstallRoot -Force | Out-Null
New-ItemProperty -Path $reg -Name "UninstallString" -Value $uninst -Force | Out-Null
New-ItemProperty -Path $reg -Name "DisplayIcon" -Value (Join-Path $InstallRoot "Daedalus.vbs") -Force | Out-Null

Write-Host ""
Write-Host "Готово. Старт: менюто → $DisplayName  или  $InstallRoot\Daedalus.cmd"
Write-Host "Studio: http://127.0.0.1:8787"
Write-Host "Ключове / офлайн: $envTarget"
Write-Host "Премахване: $InstallRoot\uninstall.ps1  (данните в $DataRoot остават)"
