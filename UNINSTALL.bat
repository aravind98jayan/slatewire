@echo off
cd /d "%~dp0"
echo This removes the Desktop shortcut and the private Python environment (.venv).
echo Your saved data (archive.json, settings.json etc. inside the slatewire folder) is NOT deleted.
echo To remove everything, delete this whole folder afterwards.
echo.
pause
powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item ([Environment]::GetFolderPath('Desktop')+'\SLATEWIRE.lnk') -ErrorAction SilentlyContinue"
schtasks /Delete /TN "SLATEWIRE Notifier" /F >nul 2>&1
if exist ".venv" rmdir /s /q ".venv"
echo Done.
pause
