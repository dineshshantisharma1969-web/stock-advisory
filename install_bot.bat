@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (python -m pip install -r bot_requirements.txt) else (py -m pip install -r bot_requirements.txt)
pause
