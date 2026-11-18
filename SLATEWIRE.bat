@echo off
cd /d "%~dp0slatewire"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo SLATEWIRE is not installed yet. Run INSTALL.bat first.
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" app.py
exit /b 0
