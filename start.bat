@echo off
rem Windows: double-click to set up (first time) and run the /status bot.
cd /d "%~dp0"
title Claude Telegram notifications
set "PYCHECK=import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"
set "PY="
python -c "%PYCHECK%" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "%PYCHECK%" >nul 2>&1 && set "PY=py -3"
if not defined PY goto no_python
%PY% install.py --if-needed
if errorlevel 1 goto end
echo.
echo Notifications are on. This window answers /status in Telegram - keep it open for that.
%PY% bot.py
goto end

:no_python
echo Python 3 is needed: https://www.python.org/downloads/ (tick "Add python.exe to PATH").

:end
pause
