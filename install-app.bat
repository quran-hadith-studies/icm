@echo off
rem One-time setup of the ICM desktop app on Windows: private Python environment + icm:// link registration.
rem Needs Python 3.9+ from https://www.python.org/downloads/ (tick "Add python.exe to PATH" or keep the "py" launcher).
setlocal
set "HERE=%~dp0"
set "VENV=%USERPROFILE%\.icm_app\venv"
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3 was not found. Install it from https://www.python.org/downloads/ and run this again.
  pause
  exit /b 1
)
if not exist "%VENV%\Scripts\python.exe" %PY% -m venv "%VENV%" || goto :fail
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip numpy || goto :fail
"%VENV%\Scripts\python.exe" -m pip install --quiet pywebview || echo (no native window available: the app will open in an Edge/Chrome window instead)
cd /d "%HERE%"
"%VENV%\Scripts\python.exe" -m icm app install || goto :fail
echo.
echo Done. On the website, open your project's waiting page and press the desktop-app button.
echo To update later: run "git pull" in %HERE%
pause
exit /b 0
:fail
echo Setup failed (see the message above).
pause
exit /b 1
