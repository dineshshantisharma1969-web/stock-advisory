@echo off
rem Daily paper-trading bot. Task Scheduler runs this at 8:30 am on weekdays.
cd /d "%~dp0"
.venv\Scripts\python.exe daily_bot.py
