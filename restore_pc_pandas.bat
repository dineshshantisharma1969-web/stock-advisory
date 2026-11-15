@echo off
rem Puts back pandas 2.3.3 in the PC's main Python (what your other tools need).
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (set PY=python) else (set PY=py)
%PY% -m pip install "pandas==2.3.3"
pause
