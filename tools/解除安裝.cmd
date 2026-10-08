@echo off
rem Double-click to uninstall: copies uninstall.ps1 to %TEMP% (so this folder can be deleted), then runs it with PowerShell.
copy /y "%~dp0uninstall.ps1" "%TEMP%\ExperimentPlanner-uninstall.ps1" >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\ExperimentPlanner-uninstall.ps1"
if errorlevel 1 pause
