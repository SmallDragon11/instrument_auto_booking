@echo off
rem Double-click to uninstall: copies uninstall.ps1 to %TEMP% and leaves this folder (so it can be deleted), then runs it with PowerShell.
rem The last command is ONE line that always ends in exit: this file gets deleted while running, so cmd must not read past it.
copy /y "%~dp0uninstall.ps1" "%TEMP%\ExperimentPlanner-uninstall.ps1" >nul
cd /d "%TEMP%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\ExperimentPlanner-uninstall.ps1" %* && exit 0 || (pause & exit 1)
