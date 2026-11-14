@echo off
rem Daily paper-trading bot. Task Scheduler runs this at 8:30 am on weekdays.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (python daily_bot.py) else (py daily_bot.py)
