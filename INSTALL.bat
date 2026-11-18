@echo off
setlocal
cd /d "%~dp0"
title SLATEWIRE installer
echo.
echo ===== SLATEWIRE installer =====
echo Folder: %~dp0
echo.

set "PY="
py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
  py -3.13 --version >nul 2>&1 && set "PY=py -3.13"
)
if not defined PY (
  py -3 --version >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  python --version >nul 2>&1 && set "PY=python"
)
if not defined PY goto :nopython

echo Using:
%PY% --version
echo.

if not exist ".venv\Scripts\python.exe" (
  echo Creating a private Python environment in .venv ...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)

echo Installing requirements (needs internet, takes 1-3 minutes) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r "slatewire\requirements.txt"
if errorlevel 1 goto :fail

echo.
echo Creating Desktop shortcut ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\SLATEWIRE.lnk'); $s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='app.py'; $s.WorkingDirectory='%~dp0slatewire'; $s.Description='SLATEWIRE cybersecurity news reader'; $s.IconLocation='%~dp0slatewire\slatewire.ico,0'; $s.Save()"
if errorlevel 1 echo (Shortcut could not be created - you can still start the app with SLATEWIRE.bat)

echo.
echo ===== Done. =====
echo Start SLATEWIRE with the Desktop shortcut or by double-clicking SLATEWIRE.bat
echo.
choice /C YN /M "Turn on background notifications now (checks every 30 min, no window)"
if errorlevel 2 goto :skipnotify
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable_notifications.ps1"
:skipnotify
echo.
pause
exit /b 0

:nopython
echo Python 3 was not found.
echo Install Python 3.12 or 3.13 from https://www.python.org/downloads/windows/
echo and TICK "Add python.exe to PATH" on the first installer screen.
echo Then run INSTALL.bat again.
echo.
pause
exit /b 1

:fail
echo.
echo Something failed. Scroll up to read the error, then see README_WINDOWS.txt.
echo If the error mentions pythonnet or a wheel, install Python 3.12 from python.org,
echo delete the folder ".venv" next to this file, and run INSTALL.bat again.
echo.
pause
exit /b 1
