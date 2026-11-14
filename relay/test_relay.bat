@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (python kotak_live_relay.py --test) else (py kotak_live_relay.py --test)
pause
