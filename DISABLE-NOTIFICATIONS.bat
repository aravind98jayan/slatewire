@echo off
cd /d "%~dp0"
schtasks /Delete /TN "SLATEWIRE Notifier" /F
echo.
echo Background notifications are off (the scheduled task was removed).
pause
