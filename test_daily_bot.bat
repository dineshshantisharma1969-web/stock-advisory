@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe daily_bot.py --dry-run
pause
