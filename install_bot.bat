@echo off
rem Installs the bot into its own folder (.venv) so other Python programs on this PC are not affected.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (set PY=python) else (set PY=py)
if not exist .venv\Scripts\python.exe %PY% -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r bot_requirements.txt
echo.
echo Done. The bot uses C:\nifty-desk\.venv - your other Python programs are unchanged.
pause
