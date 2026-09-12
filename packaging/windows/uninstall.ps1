#Requires -Version 5.1
<#
.SYNOPSIS
  Remove the Daedalus Start Menu app. Data stays unless -RemoveData.
#>
param(
    [switch] $RemoveData
)

$ErrorActionPreference = "Stop"
$AppName = "Daedalus"
$DisplayName = "Daedalus"
$DefaultInstall = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$DataRoot = Join-Path $env:LOCALAPPDATA $AppName
$InstallRoot = $DefaultInstall

$reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppName"
if (Test-Path $reg) {
    $fromReg = (Get-ItemProperty $reg -ErrorAction SilentlyContinue).InstallLocation
    if ($fromReg) { $InstallRoot = $fromReg }
}

$linkNames = @($DisplayName, "Dedal")
$linkDirs = @(
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),
    [Environment]::GetFolderPath("Desktop"),
    [Environment]::GetFolderPath("Startup")
)
foreach ($dir in $linkDirs) {
    if (-not $dir) { continue }
    foreach ($name in $linkNames) {
        $link = Join-Path $dir "$name.lnk"
        if (Test-Path $link) { Remove-Item -Force $link }
    }
}

if (Test-Path $reg) { Remove-Item -Recurse -Force $reg }

if (Test-Path $InstallRoot) {
    $resolved = [IO.Path]::GetFullPath($InstallRoot)
    $allowed = [IO.Path]::GetFullPath($DefaultInstall)
    if ($resolved -eq $allowed) {
        Write-Host "Removing $resolved"
        Remove-Item -Recurse -Force $resolved
    }
    else {
        foreach ($rel in @(".venv", "runtime", "Daedalus.exe")) {
            $p = Join-Path $resolved $rel
            if (Test-Path $p) {
                Remove-Item -Recurse -Force $p
                Write-Host "Removed $p"
            }
        }
        Write-Host "Source tree at $resolved was not deleted (not %LOCALAPPDATA%\Programs\Daedalus)."
    }
}

if ($RemoveData -and (Test-Path $DataRoot)) {
    Write-Host "Removing data $DataRoot"
    Remove-Item -Recurse -Force $DataRoot
}
else {
    Write-Host "Data kept in $DataRoot (memory, games, .env). Add -RemoveData to wipe it."
}

Write-Host "Daedalus was removed from the Start Menu."
