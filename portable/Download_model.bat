@echo off
setlocal
chcp 65001 >nul
title LocalAIforSPECheck - Download Model
set "PYTHONUTF8=1"
if not exist "%~dp0runtime\python.exe" (
  echo Please extract the complete Portable ZIP before running this file.
  pause
  exit /b 1
)
"%~dp0runtime\python.exe" "%~dp0app\download_model.py" %*
set "PORTABLE_EXIT=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %PORTABLE_EXIT%
