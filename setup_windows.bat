@echo off
REM One-time setup for the Wound Healing batch tool (Windows).
REM Creates a private Python environment in this folder and installs the packages it needs.
cd /d "%~dp0"
echo.
echo === Wound Healing batch tool: setup ===
echo.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto oldpython
echo Creating the tool's Python environment...
%PY% -m venv .venv
if errorlevel 1 goto failed
echo Installing packages (this takes a few minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo === Setup complete. You can now double-click Run_Wound_Analysis.bat ===
echo.
pause
exit /b 0

:nopython
echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" on the first installer screen, then run this file again.
pause
exit /b 1

:oldpython
echo Your Python is older than 3.10. Install a newer version from https://www.python.org/downloads/
pause
exit /b 1

:failed
echo.
echo Setup FAILED. Check your internet connection, then run this file again.
echo If it keeps failing, send a screenshot of this window.
pause
exit /b 1
