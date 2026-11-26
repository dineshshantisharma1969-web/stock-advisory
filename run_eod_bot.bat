@echo off
rem End-of-day P&L and carried-forward calls. Task Scheduler: weekdays at 3:45 pm.
rem Does not place trades. The morning job stays on run_daily_bot.bat.
cd /d "%~dp0"
.venv\Scripts\python.exe daily_bot.py --eod
