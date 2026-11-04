@echo off
REM One-time setup for the Wound Healing batch tool (Windows).
REM Creates a private Python environment in this folder and installs the packages it needs.
cd /d "%~dp0"
echo.
echo === Wound Healing batch tool: setup ===
echo.
REM Use a Python version that every package has ready-made downloads for (3.10 to 3.14).
REM Newer versions (3.15+) are skipped until the image-analysis packages support them.
set "PY="
for %%V in (3.13 3.12 3.14 3.11 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython
echo Using:
%PY% --version
echo Creating the tool's Python environment...
%PY% -m venv --clear .venv
if errorlevel 1 goto failed
echo Installing packages (this takes a few minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install --only-binary=:all: -r requirements.txt
if errorlevel 1 goto failed
echo.
echo === Setup complete. You can now double-click Run_Wound_Analysis.bat ===
echo.
pause
exit /b 0

:nopython
echo No compatible Python found.
echo.
echo The tool needs Python 3.10 to 3.14. Newer versions such as 3.15 are not supported yet,
echo because one of the image-analysis packages has not released a version for them.
echo.
echo Fix: open a new Command Prompt and type:   py install 3.13
echo (or download Python 3.13 from https://www.python.org/downloads/windows/ ).
echo You do not need to uninstall your current Python. Then run this file again.
pause
exit /b 1

:failed
if exist .venv rmdir /s /q .venv
echo.
echo Setup FAILED.
echo If the messages above mention "No matching distribution", send a screenshot of this window.
echo Otherwise check your internet connection and run this file again.
pause
exit /b 1
