@echo off
setlocal
cd /d "%~dp0"

echo.
echo  JARVIS DESKTOP COMPANION
echo  Installing optional Windows desktop controls...
echo.

py -3.12 -m pip install -e ".[desktop]"
if errorlevel 1 (
  echo.
  echo Installation failed. Make sure Python 3.12 is installed and on PATH.
  echo You can also run: python -m pip install -e ".[desktop]"
  pause
  exit /b 1
)

echo.
echo Starting the local permission-gated desktop companion...
echo Keep this window open while using Desktop Agent in JARVIS.
echo.
py -3.12 -m agents.desktop_agent.local_bridge
if errorlevel 1 (
  echo.
  echo The companion stopped with an error. Read the message above.
  pause
  exit /b 1
)
endlocal
