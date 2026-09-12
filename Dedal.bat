@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Dedal

if not exist "daedalus\agent.py" (
  echo.
  echo  Greshka: otvori papkata kudeto e README.md i Dedal.bat
  echo  (ne v parent papkata ot ZIP-a)
  echo.
  pause
  exit /b 1
)

where py >nul 2>&1
if errorlevel 1 (
  where python >nul 2>&1
  if errorlevel 1 (
    echo.
    echo  Nqma Python. Instalirai ot python.org
    echo  Vazhno: markiraj "Add python.exe to PATH"
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
  )
  set "PY=python"
) else (
  set "PY=py -3"
)

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  Purvi pat - instaliram zavisimosti. 3-10 minuti, ne zatvariai prozorca.
  echo.
  %PY% -m venv .venv
  if errorlevel 1 (
    echo venv ne uspq.
    pause
    exit /b 1
  )
  call .venv\Scripts\activate.bat
  python -m pip install -U pip
  pip install -e .
  if errorlevel 1 (
    echo pip install ne uspq.
    pause
    exit /b 1
  )
) else (
  call .venv\Scripts\activate.bat
)

if not exist "%LOCALAPPDATA%\Daedalus" mkdir "%LOCALAPPDATA%\Daedalus"
if not exist "%LOCALAPPDATA%\Daedalus\playground" mkdir "%LOCALAPPDATA%\Daedalus\playground"
if not exist "%LOCALAPPDATA%\Daedalus\.env" (
  copy /Y .env.example "%LOCALAPPDATA%\Daedalus\.env" >nul
)

set DAEDALUS_STANDALONE=1
set DAEDALUS_DATA=%LOCALAPPDATA%\Daedalus
set DAEDALUS_PLAYGROUND=%LOCALAPPDATA%\Daedalus\playground
set HOST=127.0.0.1
set PORT=8787

echo.
echo  Dedal raboti na:  http://127.0.0.1:8787
echo  API kluch (.env): %LOCALAPPDATA%\Daedalus\.env
echo  Za da spresh: zatvori tozi prozorec
echo.
start "" "http://127.0.0.1:8787"
python -m app.cli serve --host 127.0.0.1 --port 8787
pause
