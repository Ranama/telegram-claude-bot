@echo off
rem Claude-Telegram bot: double-click to set up (first time) and start the bot.
rem Everything else (installing and signing in Claude Code, the bot token) is done
rem by setup_bot.py in this same window. Its log is setup.log, the bot's is bot.log.
setlocal EnableExtensions
cd /d "%~dp0"
title Claude Telegram bot

rem Find a working Python 3.8+. The Microsoft Store "python" shortcut doesn't count.
set "PYCHECK=import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"
set "PY="
python -c "%PYCHECK%" >nul 2>&1 && set "PY=python"
if defined PY goto run
py -3 -c "%PYCHECK%" >nul 2>&1 && set "PY=py -3"
if defined PY goto run
call :find_python
if defined PY goto run

echo Python 3 is needed to run the bot and was not found on this PC.
where winget >nul 2>&1 || goto no_python
choice /c YN /m "Install Python 3.12 now (with winget)"
if errorlevel 2 goto no_python
winget install -e --id Python.Python.3.12
call :find_python
if not defined PY goto no_python

:run
%PY% setup_bot.py config.json
echo.
echo The bot has stopped. (Details: setup.log and bot.log in this folder.)
pause
exit /b

:find_python
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY="%%D\python.exe""
exit /b

:no_python
echo.
echo [X] Python 3 is required. Install it from https://www.python.org/downloads/
echo     (tick "Add python.exe to PATH"), then double-click start.bat again.
pause
exit /b 1
