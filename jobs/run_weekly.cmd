@echo off
rem Saturday: refresh company financials for all NIFTY 500 stocks, then re-rate. Run by Task Scheduler
rem (task "StockAdvisory\WeeklyFundamentals"). Output is appended to data\refresh.log.
cd /d "C:\Users\Dinesh Sharma\Desktop\STOCK_ADVISORY_APP"
echo ===== %date% %time% weekly fundamentals >> data\refresh.log
".venv\Scripts\python.exe" jobs\fundamentals.py >> data\refresh.log 2>&1
".venv\Scripts\python.exe" jobs\backtest_index.py >> data\refresh.log 2>&1
".venv\Scripts\python.exe" jobs\refresh.py >> data\refresh.log 2>&1
".venv\Scripts\python.exe" jobs\push_snapshot.py >> data\refresh.log 2>&1
echo ===== exit code %errorlevel% >> data\refresh.log
