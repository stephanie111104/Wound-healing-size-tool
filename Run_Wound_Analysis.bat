@echo off
REM Run the Wound Healing batch analysis (Windows). Opens a window to choose the plate folder.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The tool is not set up yet. Double-click setup_windows.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" run_wound_analysis.py %*
echo.
pause
