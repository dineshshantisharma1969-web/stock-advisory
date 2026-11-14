@echo off
rem Starts the Kotak live-price relay. Task Scheduler runs this at 9:05 am on weekdays.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (python kotak_live_relay.py) else (py kotak_live_relay.py)
