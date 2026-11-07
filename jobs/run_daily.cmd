@echo off
rem Weekday evening refresh: prices, breadth, macro, ratings, and the ratings log. Run by Task Scheduler
rem (task "StockAdvisory\DailyRefresh"). Output is appended to data\refresh.log.
cd /d "C:\Users\Dinesh Sharma\Desktop\STOCK_ADVISORY_APP"
echo ===== %date% %time% daily refresh >> data\refresh.log
".venv\Scripts\python.exe" jobs\refresh.py >> data\refresh.log 2>&1
".venv\Scripts\python.exe" jobs\push_snapshot.py >> data\refresh.log 2>&1
echo ===== exit code %errorlevel% >> data\refresh.log
