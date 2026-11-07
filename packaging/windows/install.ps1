#Requires -Version 5.1
<#
.SYNOPSIS
  Install Daedalus as a per-user Windows app (Start Menu, no Administrator).

.DESCRIPTION
  Copies the product to %LOCALAPPDATA%\Programs\Daedalus, creates a venv,
  pip-installs, writes .cmd/.vbs launchers (optional Daedalus.exe), and Start Menu shortcuts.

  If Python 3.11+ is not on PATH, a private CPython (NuGet python package) is
  downloaded into the install folder. It is never added to PATH.

  Data (memory, playground, .env) lives in %LOCALAPPDATA%\Daedalus — kept
  across reinstalls.

.PARAMETER InPlace
  Product files are already at the install location (used by Inno Setup).

.PARAMETER BundlePython
  Always install/use the private CPython, even if a system Python exists.
  The Setup.exe path uses this so the app does not depend on PATH.

.PARAMETER UseSystemPython
  Never download CPython. Fail if Python 3.11+ is not on PATH (developer mode).

.PARAMETER DesktopShortcut
  Also put a shortcut on this user's desktop.

.PARAMETER Startup
  Launch Daedalus when Windows signs in.
#>
param(
    [switch] $InPlace,
    [switch] $BundlePython,
    [switch] $UseSystemPython,
    [switch] $DesktopShortcut,
    [switch] $Startup
)

$ErrorActionPreference = "Stop"
$AppName = "Daedalus"
$DisplayName = "Daedalus"
$InstallRoot = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$DataRoot = Join-Path $env:LOCALAPPDATA $AppName
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

. (Join-Path $PSScriptRoot "Get-BundledPython.ps1")

function Test-DaedalusPython311 {
    <#
      PS 5.1: probing with "& py ... 2>$null" can fault on stderr from the launcher.
      Use Start-Process with stdout/stderr redirects instead (no native PowerShell stderr pipe).
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string] $ExePath,
        [string[]] $PrefixArgs = @()
    )
    $code = 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
    $argList = [System.Collections.ArrayList]::new()
    foreach ($a in $PrefixArgs) { [void]$argList.Add($a) }
    [void]$argList.Add('-c')
    [void]$argList.Add($code)
    $stdout = Join-Path ([IO.Path]::GetTempPath()) ('daedalus-py-out-' + [Guid]::NewGuid().ToString('n'))
    $stderr = Join-Path ([IO.Path]::GetTempPath()) ('daedalus-py-err-' + [Guid]::NewGuid().ToString('n'))
    try {
        $p = Start-Process -FilePath $ExePath -ArgumentList @($argList.ToArray()) `
            -NoNewWindow -PassThru -Wait -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        if (-not $p) { return $false }
        return ($p.ExitCode -eq 0)
    }
    catch {
        return $false
    }
    finally {
        Remove-Item -Force $stdout, $stderr -ErrorAction SilentlyContinue
    }
}

function Find-SystemPython {
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
        $exePath = $cmd.Source
        if (-not $exePath) { continue }
        if (Test-DaedalusPython311 -ExePath $exePath -PrefixArgs $c.Args) {
            return @{ Exe = $c.Exe; Args = @($c.Args) }
        }
    }
    return $null
}

function Resolve-Python {
    param(
        [string] $Root,
        [switch] $ForceBundle,
        [switch] $SystemOnly
    )
    $bundledExe = Join-Path $Root "runtime\python.exe"
    if ($SystemOnly) {
        $sys = Find-SystemPython
        if ($sys) { return $sys }
        throw "Python 3.11+ not found on PATH. Install it from python.org or omit -UseSystemPython to use the bundled runtime."
    }
    if ($ForceBundle) {
        Install-DaedalusBundledPython -RuntimeRoot (Join-Path $Root "runtime") -RedistDir (Join-Path $PSScriptRoot "redist")
        return @{ Exe = $bundledExe; Args = @() }
    }
    $sys = Find-SystemPython
    if ($sys) { return $sys }
    Write-Host "No Python 3.11+ on PATH - installing a private CPython for Daedalus (not added to PATH)."
    Install-DaedalusBundledPython -RuntimeRoot (Join-Path $Root "runtime") -RedistDir (Join-Path $PSScriptRoot "redist")
    return @{ Exe = $bundledExe; Args = @() }
}

function Invoke-RobocopyCopy([string] $From, [string] $To) {
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    $xd = @(".git", ".venv", "data", "playground", "__pycache__", ".pytest_cache", "daedalus.egg-info", ".agents", "dist", "runtime", "redist")
    & robocopy $From $To /E /NFL /NDL /NJH /NJS /NP /XD @xd /XF "*.pyc" ".env" | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit $LASTEXITCODE"
    }
}

function Write-CmdAndVbs([string] $Root) {
    $cmdPath = Join-Path $Root "Daedalus.cmd"
    $vbsPath = Join-Path $Root "Daedalus.vbs"
    $pyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    # PS 5.1 here-strings must start at column 0; use line arrays instead.
    $cmdLines = @(
        '@echo off'
        'setlocal'
        'cd /d "%~dp0"'
        'set PYTHONUTF8=1'
        'set PYTHONIOENCODING=utf-8'
        'set DAEDALUS_STANDALONE=1'
        'set DAEDALUS_WINDOWS_APP=1'
        'set DAEDALUS_DATA=%LOCALAPPDATA%\Daedalus'
        'set DAEDALUS_PLAYGROUND=%LOCALAPPDATA%\Daedalus\playground'
        'set HOST=127.0.0.1'
        'set PORT=8787'
        "`"$py`" -m app.cli desktop"
    )
    ($cmdLines -join "`r`n") | Set-Content -Encoding ASCII -Path $cmdPath
    $vbsLines = @(
        'Set sh = CreateObject("WScript.Shell")'
        'sh.Environment("Process")("PYTHONUTF8") = "1"'
        'sh.Environment("Process")("PYTHONIOENCODING") = "utf-8"'
        "sh.CurrentDirectory = `"$Root`""
        "sh.Run `"`"`"$pyw`"`" -m app.cli desktop`", 0, False"
    )
    ($vbsLines -join "`r`n") | Set-Content -Encoding ASCII -Path $vbsPath
}

function Write-DaedalusExe([string] $Root) {
    $cs = Join-Path $PSScriptRoot "DaedalusLauncher.cs"
    $out = Join-Path $Root "Daedalus.exe"
    if (-not (Test-Path $cs)) {
        Write-Host "Launcher source missing ($cs) - Start Menu will use Daedalus.vbs." -ForegroundColor Yellow
        return $false
    }
    $code = Get-Content -Raw -Encoding UTF8 $cs
    try {
        Add-Type -TypeDefinition $code -ReferencedAssemblies @("System.dll", "System.Windows.Forms.dll") -OutputAssembly $out -OutputType WindowsApplication -ErrorAction Stop
        if (Test-Path $out) { return $true }
    }
    catch {
        Write-Host "Add-Type launcher compile failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    if (-not (Test-Path $csc)) {
        $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
    }
    if (Test-Path $csc) {
        $copied = Join-Path $env:TEMP "DaedalusLauncher.cs"
        Copy-Item -Force $cs $copied
        & $csc /nologo /target:winexe /optimize "/out:$out" /r:System.Windows.Forms.dll $copied
        if ($LASTEXITCODE -eq 0 -and (Test-Path $out)) { return $true }
        Write-Host "csc.exe launcher compile failed (exit $LASTEXITCODE)." -ForegroundColor Yellow
    }
    return $false
}

function New-DaedalusShortcut([string] $LinkPath, [string] $Target) {
    $dir = Split-Path -Parent $LinkPath
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $w = New-Object -ComObject WScript.Shell
    $s = $w.CreateShortcut($LinkPath)
    $s.TargetPath = $Target
    $s.WorkingDirectory = $InstallRoot
    $s.WindowStyle = 7
    $s.Description = "$DisplayName - coding agent (Studio)"
    $iconIco = Join-Path $InstallRoot "packaging\windows\daedalus.ico"
    if (-not (Test-Path $iconIco)) {
        $iconIco = Join-Path $InstallRoot "app\static\favicon.ico"
    }
    if (Test-Path $iconIco) {
        $s.IconLocation = $iconIco
    }
    $s.Save()
}

Write-Host "=== $DisplayName - Windows install ==="
if ($InPlace) {
    $InstallRoot = $RepoRoot
}
else {
    Write-Host "Copying product to $InstallRoot"
    Invoke-RobocopyCopy -From $RepoRoot -To $InstallRoot
}

$py = Resolve-Python -Root $InstallRoot -ForceBundle:$BundlePython -SystemOnly:$UseSystemPython

New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot "playground") | Out-Null
$envExample = Join-Path $InstallRoot ".env.example"
$envTarget = Join-Path $DataRoot ".env"
if ((Test-Path $envExample) -and -not (Test-Path $envTarget)) {
    Copy-Item $envExample $envTarget
    Write-Host "Created $envTarget - add an API key or use Ollama."
}

$venv = Join-Path $InstallRoot ".venv"
Write-Host "venv + pip install (first run can take a few minutes)..."
$venvArgs = @($py.Args) + @("-m", "venv", $venv)
& $py.Exe @venvArgs
if ($LASTEXITCODE -ne 0) { throw "venv failed" }
$venvPy = Join-Path $venv "Scripts\python.exe"
& $venvPy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $venvPy -m pip install -e $InstallRoot
if ($LASTEXITCODE -ne 0) { throw "pip install daedalus failed" }
Write-Host "Installing GUI extra (pywebview / Edge WebView2)..."
$guiOk = $false
Push-Location $InstallRoot
try {
    & $venvPy -m pip install -e ".[gui]"
    if ($LASTEXITCODE -eq 0) { $guiOk = $true }
}
finally {
    Pop-Location
}
if (-not $guiOk) {
    Write-Host "pywebview did not install - Tk or the browser will be used." -ForegroundColor Yellow
}

Write-CmdAndVbs -Root $InstallRoot
$srcUn = Join-Path $PSScriptRoot "uninstall.ps1"
$dstUn = Join-Path $InstallRoot "uninstall.ps1"
if ((Resolve-Path $srcUn).Path -ne $(if (Test-Path $dstUn) { (Resolve-Path $dstUn).Path } else { "" })) {
    Copy-Item -Force $srcUn $dstUn
}

$null = Write-DaedalusExe -Root $InstallRoot
# Shortcuts must not target unsigned Daedalus.exe — SmartScreen/Defender may flag it.
# Preferred: Daedalus.vbs (pythonw, no console). Fallback: Daedalus.cmd.
$vbsLaunch = Join-Path $InstallRoot "Daedalus.vbs"
$cmdLaunch = Join-Path $InstallRoot "Daedalus.cmd"
$launch = if (Test-Path $vbsLaunch) { $vbsLaunch } else { $cmdLaunch }
if (-not (Test-Path $launch)) {
    throw "Launch script missing: expected $vbsLaunch or $cmdLaunch"
}

$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-DaedalusShortcut (Join-Path $startMenu "$DisplayName.lnk") $launch
# Latin alias keeps PS 5.1 happy on non-UTF-8 code pages; same Start Menu entry.
New-DaedalusShortcut (Join-Path $startMenu "Dedal.lnk") $launch
if ($DesktopShortcut) {
    New-DaedalusShortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "$DisplayName.lnk") $launch
}
if ($Startup) {
    $startupDir = [Environment]::GetFolderPath("Startup")
    New-DaedalusShortcut (Join-Path $startupDir "$DisplayName.lnk") $launch
}

$uninst = 'powershell.exe -ExecutionPolicy Bypass -File "' + (Join-Path $InstallRoot "uninstall.ps1") + '"'
$reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppName"
New-Item -Path $reg -Force | Out-Null
New-ItemProperty -Path $reg -Name "DisplayName" -Value $DisplayName -Force | Out-Null
New-ItemProperty -Path $reg -Name "DisplayVersion" -Value "0.3.4" -Force | Out-Null
New-ItemProperty -Path $reg -Name "Publisher" -Value "Blaue-Ente" -Force | Out-Null
New-ItemProperty -Path $reg -Name "InstallLocation" -Value $InstallRoot -Force | Out-Null
New-ItemProperty -Path $reg -Name "UninstallString" -Value $uninst -Force | Out-Null
New-ItemProperty -Path $reg -Name "DisplayIcon" -Value $launch -Force | Out-Null

Write-Host ""
Write-Host "Done. Start: Start Menu -> $DisplayName (shortcut -> $(Split-Path -Leaf $launch))"
if (Test-Path (Join-Path $InstallRoot "Daedalus.exe")) {
    Write-Host "Optional unsigned launcher (may be flagged by Defender if run directly): $InstallRoot\Daedalus.exe"
}
Write-Host "Console logs: $InstallRoot\Daedalus.cmd"
if ($guiOk) {
    Write-Host "Window: Edge WebView2 (pywebview)"
} else {
    Write-Host "Window: Tk panel or browser (pywebview not available)"
}
Write-Host "Studio: http://127.0.0.1:8787"
Write-Host "Keys / offline: $envTarget"
Write-Host "Remove: $InstallRoot\uninstall.ps1  (data in $DataRoot is kept)"
