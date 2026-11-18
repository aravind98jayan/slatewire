@echo off
cd /d "%~dp0"
title SLATEWIRE - turn on background notifications
echo.
echo This adds a small hidden Windows task that checks the news every 30 minutes and
echo shows an "IMPORTANT" notification for new critical articles and a "Catch up"
echo notification if you have not opened SLATEWIRE for 6+ hours. Nothing starts with Windows;
echo the task only runs while you are logged in and does nothing while the app is open.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable_notifications.ps1"
echo.
pause
