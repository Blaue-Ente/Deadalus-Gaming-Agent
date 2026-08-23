#Requires -Version 5.1
<#
.SYNOPSIS
  Remove the Дедал Start Menu app. Data stays unless -RemoveData.
#>
param(
    [switch] $RemoveData
)

$ErrorActionPreference = "Stop"
$AppName = "Daedalus"
$DisplayName = "Дедал"
$DefaultInstall = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$DataRoot = Join-Path $env:LOCALAPPDATA $AppName
$InstallRoot = $DefaultInstall

$reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppName"
if (Test-Path $reg) {
    $fromReg = (Get-ItemProperty $reg -ErrorAction SilentlyContinue).InstallLocation
    if ($fromReg) { $InstallRoot = $fromReg }
}

$links = @(
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$DisplayName.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "$DisplayName.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Startup")) "$DisplayName.lnk")
)
foreach ($link in $links) {
    if (Test-Path $link) { Remove-Item -Force $link }
}

if (Test-Path $reg) { Remove-Item -Recurse -Force $reg }

if (Test-Path $InstallRoot) {
    $resolved = [IO.Path]::GetFullPath($InstallRoot)
    $allowed = [IO.Path]::GetFullPath($DefaultInstall)
    if ($resolved -eq $allowed) {
        Write-Host "Премахвам $resolved"
        Remove-Item -Recurse -Force $resolved
    }
    else {
        $venv = Join-Path $resolved ".venv"
        if (Test-Path $venv) {
            Remove-Item -Recurse -Force $venv
            Write-Host "Махнат venv в $venv"
        }
        Write-Host "Кодът в $resolved не е изтрит (не е %LOCALAPPDATA%\Programs\Daedalus)."
    }
}

if ($RemoveData -and (Test-Path $DataRoot)) {
    Write-Host "Премахвам данни $DataRoot"
    Remove-Item -Recurse -Force $DataRoot
}
else {
    Write-Host "Данните остават в $DataRoot (памет, игри, .env). Изтрий с -RemoveData ако искаш."
}

Write-Host "Дедал е махнат от менюто."
