@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (python daily_bot.py --dry-run) else (py daily_bot.py --dry-run)
pause
