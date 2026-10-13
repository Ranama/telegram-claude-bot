@echo off
cd /d "%~dp0"
if not exist config.json (
  copy config.example.json config.json >nul
  echo Created config.json - open it, paste your bot token and Telegram user id, then run start.bat again.
  notepad config.json
  exit /b
)
python bot.py config.json
pause
