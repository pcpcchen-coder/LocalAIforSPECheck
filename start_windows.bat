@echo off
setlocal
cd /d "%~dp0"
title LocalAIforSPECheck
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" launcher.py
    goto done
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 launcher.py
    goto done
)
where python >nul 2>nul
if not errorlevel 1 (
    python launcher.py
    goto done
)
echo Python was not found. Please ask IT to follow README.md first.
:done
if errorlevel 1 (
    echo.
    echo Application did not start. Please send this window to IT.
    pause
)
endlocal
