@echo off
REM ===================================================================
REM  HES - native desktop window (Windows)
REM
REM  Same application as the browser launcher, in its own window rather
REM  than a browser tab. Needs pywebview; if it is not installed this
REM  offers to install it, and opens your browser if you decline.
REM ===================================================================
setlocal
title HES - Hybrid Energy System Sizing
cd /d "%~dp0"

echo.
echo   HES - Hybrid Energy System Sizing (desktop)
echo   ============================================
echo   Folder: %CD%
echo.

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY ( where python3 >nul 2>&1 && set "PY=python3" )

if not defined PY (
  echo   [X] Python was not found on this computer.
  echo.
  echo       Install Python 3.9 or newer from python.org, and tick
  echo       "Add Python to PATH" during installation. Then run this
  echo       file again.
  echo.
  pause
  exit /b 1
)

echo   Using: %PY%
%PY% --version
echo.

if not exist "desktop.py" (
  echo   [X] desktop.py is not in this folder.
  echo       Run this file from inside the HES folder.
  echo.
  pause
  exit /b 1
)

%PY% -c "import webview" >nul 2>&1
if errorlevel 1 (
  echo   pywebview is not installed. It is the only optional dependency
  echo   in the whole project, and only this window needs it.
  echo.
  choice /c YN /m "   Install it now"
  if errorlevel 2 goto run
  %PY% -m pip install pywebview
  echo.
)

:run
echo   Starting. Close the window to stop.
echo   ============================================
echo.
%PY% desktop.py

echo.
echo   Stopped.
pause
endlocal
