@echo off
setlocal
cd /d "%~dp0"

echo.
echo  JARVIS DESKTOP COMPANION
echo  Checking for a usable Python runtime...
echo.

set "PYTHON_CMD="

rem Prefer Python 3.12, but also accept any newer Python supported by pyproject.toml.
py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.12"

if not defined PYTHON_CMD (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD call :try_python python
if not defined PYTHON_CMD call :try_python python3

if not defined PYTHON_CMD goto :python_missing

echo Using Python command: %PYTHON_CMD%
echo.
echo Installing optional Windows desktop controls...
%PYTHON_CMD% -m pip --version >nul 2>&1
if errorlevel 1 (
  echo Python was found, but pip is unavailable. Trying to enable pip...
  %PYTHON_CMD% -m ensurepip --upgrade
  if errorlevel 1 goto :install_failed
)

%PYTHON_CMD% -m pip install -e ".[desktop]"
if errorlevel 1 goto :install_failed

echo.
echo Starting the local permission-gated desktop companion...
echo Keep this window open while using Desktop Agent in JARVIS.
echo.
%PYTHON_CMD% -m agents.desktop_agent.local_bridge
if errorlevel 1 (
  echo.
  echo The companion stopped with an error. Read the message above.
  pause
  exit /b 1
)
endlocal
exit /b 0

:try_python
if defined PYTHON_CMD exit /b 0
%~1 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=%~1"
exit /b 0

:python_missing
echo.
echo No usable Python 3.12-or-newer runtime was found.
echo.
echo Try these commands and check their output:
echo   py -0p
echo   python --version
echo   where python
echo.
echo Install Python 3.12 or newer from https://www.python.org/downloads/windows/
echo During installation, enable "Add python.exe to PATH", then reopen this window.
pause
exit /b 1

:install_failed
echo.
echo Dependency installation failed. Check the error above.
echo You can retry manually with:
echo   %PYTHON_CMD% -m pip install -e ".[desktop]"
pause
exit /b 1
