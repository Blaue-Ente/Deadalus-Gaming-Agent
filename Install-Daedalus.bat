@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Daedalus - Install
echo.
echo  Daedalus / Dedal - Windows install
echo  ===================================
echo.
echo  This copies Daedalus to your user folder, installs Python deps,
echo  and adds Start Menu + Desktop shortcuts. No Administrator.
echo  First run needs internet (pip). It can take a few minutes.
echo.

set "INSTALL_PS1=%~dp0packaging\windows\install.ps1"
if not exist "%INSTALL_PS1%" (
  echo ERROR: Missing %INSTALL_PS1%
  echo Unzip the full repo folder, then run this file again.
  pause
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "Unblock-File -LiteralPath '%INSTALL_PS1%' -ErrorAction SilentlyContinue; ^
   & '%INSTALL_PS1%' -DesktopShortcut"
set "ERR=%ERRORLEVEL%"

echo.
if not "%ERR%"=="0" (
  echo Install failed with exit code %ERR%.
  echo If you see a red error above, copy it and ask for help.
  pause
  exit /b %ERR%
)

echo Done.
echo   Start Menu -^> Daedalus  (or Dedal)
echo   Studio: http://127.0.0.1:8787
echo   Keys:   %LOCALAPPDATA%\Daedalus\.env
echo.
pause
exit /b 0
