@echo off
REM ===================================================================
REM  HES launcher for Windows
REM
REM  Double-click this file. It finds Python, starts the local server
REM  and opens your browser.
REM
REM  The window stays open on purpose: if anything goes wrong, the
REM  error is on screen instead of vanishing with a console that closes
REM  itself. Close this window to stop the server.
REM ===================================================================
setlocal
cd /d "%~dp0"

echo.
echo   HES - Hybrid Energy System Sizing
echo   ============================================
echo   Folder: %CD%
echo.

REM --- find a Python interpreter -------------------------------------
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

REM --- sanity check: are the engine files actually here? --------------
if not exist "server.py" (
  echo   [X] server.py is not in this folder.
  echo       Run this file from inside the EnergyManagement folder.
  echo.
  pause
  exit /b 1
)
if not exist "ensys\__init__.py" (
  echo   [X] The 'ensys' engine folder is missing or incomplete.
  echo.
  pause
  exit /b 1
)

REM --- run the self-check first, so problems are named ---------------
echo   Running self-check...
%PY% doctor.py --quiet
if errorlevel 1 (
  echo.
  echo   [X] The self-check found a problem. Details are above.
  echo.
  pause
  exit /b 1
)

echo.
echo   Starting the server. Your browser should open shortly.
echo   If it does not, open this address yourself:
echo.
echo       http://127.0.0.1:8756/
echo.
echo   If the page stops on the loading screen, it will name the
echo   stage it is waiting at after a few seconds - send that text.
echo.
echo   Leave this window open while you use HES.
echo   Press Ctrl+C, or close this window, to stop.
echo   ============================================
echo.

%PY% server.py

echo.
echo   Server stopped.
pause
endlocal
