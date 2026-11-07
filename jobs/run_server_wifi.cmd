@echo off
rem Start the app so your phone can open it over home Wi-Fi. Keep this window open while you use the app.
rem Needs a password first: create the file config\app_password.txt containing one line with your chosen password.
cd /d "C:\Users\Dinesh Sharma\Desktop\STOCK_ADVISORY_APP"
if not exist config\app_password.txt (
  echo Create config\app_password.txt with your app password on one line, then run this again.
  pause
  exit /b 1
)
set /p APP_PASSWORD=<config\app_password.txt
echo.
echo On your phone (same Wi-Fi), open one of these addresses, then use the browser menu "Add to Home screen":
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address"') do echo    http:/%%a:8000
echo.
echo If Windows asks whether to allow Python on networks, allow it for PRIVATE networks only.
echo.
".venv\Scripts\python.exe" -m uvicorn server.app:app --host 0.0.0.0 --port 8000
