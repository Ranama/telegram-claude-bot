@echo off
setlocal
cd /d "%~dp0"
title Claude Telegram bot

where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python is not installed.
  echo     Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  echo     Then run start.bat again.
  pause & exit /b 1
)

rem Claude Code: on PATH, or in the native installer's default folder.
where claude >nul 2>nul
if errorlevel 1 (
  if exist "%USERPROFILE%\.local\bin\claude.exe" (
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  ) else (
    echo [X] Claude Code is not installed. The Claude desktop app does not include it.
    echo     1. Open PowerShell and run:  irm https://claude.ai/install.ps1 ^| iex
    echo     2. Open a NEW PowerShell window and run:  claude
    echo        Log in in the browser when asked, then type /exit
    echo     3. Run start.bat again.
    pause & exit /b 1
  )
)

if not exist config.json (
  copy config.example.json config.json >nul
)

findstr /c:"PASTE-TOKEN-FROM-BOTFATHER" config.json >nul
if not errorlevel 1 (
  echo [!] Paste your bot token from @BotFather into config.json.
  echo     Notepad is opening - replace PASTE-TOKEN-FROM-BOTFATHER, save, close Notepad.
  notepad config.json
  findstr /c:"PASTE-TOKEN-FROM-BOTFATHER" config.json >nul
  if not errorlevel 1 (
    echo [X] The token is still missing. Run start.bat again when you have it.
    pause & exit /b 1
  )
)

echo [OK] Starting the bot. Keep this window open - closing it stops the bot.
echo.
python bot.py config.json
echo.
echo The bot stopped.
pause
