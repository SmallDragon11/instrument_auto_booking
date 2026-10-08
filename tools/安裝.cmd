@echo off
rem Double-click to install: runs install.ps1 in this folder with PowerShell.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
