@echo off
cd /d "%~dp0"
set "PY="
for %%C in (py python python3) do (
  if not defined PY (
    %%C --version >nul 2>&1 && set "PY=%%C"
  )
)
if not defined PY (
  for /d %%D in ("%LocalAppData%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do (
    if exist "%%~D\python.exe" if not defined PY set "PY=%%~D\python.exe"
  )
)
if not defined PY (
  echo Python was not found. Install it from python.org/downloads and tick "Add python.exe to PATH".
  echo Then run: py -m pip install -r requirements.txt
  pause
  exit /b 1
)
echo Using: %PY%
"%PY%" -c "import numpy, scipy, PIL" >nul 2>&1 || (
  echo Installing required packages ...
  "%PY%" -m pip install -r requirements.txt
)
echo Starting MiniPrint GUI - a browser tab will open. Close this window to stop.
"%PY%" -m miniprint.server
pause
