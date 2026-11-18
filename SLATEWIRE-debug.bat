@echo off
cd /d "%~dp0slatewire"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo SLATEWIRE is not installed yet. Run INSTALL.bat first.
  pause
  exit /b 1
)
echo Starting SLATEWIRE with a console so errors are visible. Close this window to quit.
"%~dp0.venv\Scripts\python.exe" app.py
echo.
echo SLATEWIRE has stopped.
pause
