@echo off
rem Drag an STL file onto this file to generate supports for it.
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
if "%~1"=="" (
  echo Drag an STL file onto run_miniprint.bat
  pause
  exit /b
)
"%PY%" -m miniprint %*
echo.
pause
