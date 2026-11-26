# Daily paper-trading bot (office PC)

Every weekday at 9:05 am the bot updates prices, scores every paper trade, squares off positions whose stop,
target or exit rule was hit, places the day's new paper trades for the three rule-based books, and saves a
WhatsApp-ready P&L summary in the app (Paper trading -> "Bot summary"). It never places a real order.

## One-time setup (about 20 minutes)
1. Unzip the app folder and copy it to `C:\nifty-desk` (so `C:\nifty-desk\daily_bot.py` exists).
2. Double-click `install_bot.bat` - it installs the packages the bot needs into its own folder `.venv`
   (2-5 minutes), so other Python programs on the PC are not affected.
3. Make a secret key just for this PC: supabase.com -> your project -> Project Settings -> API Keys ->
   Secret keys -> "New secret key", name it `office-pc`, copy it.
4. Copy `bot_settings_TEMPLATE.ini` to `bot_settings.ini`, open it in Notepad, paste the key on the
   `secret_key =` line, save. Never send this file or a picture of it to anyone.
5. Double-click `test_daily_bot.bat`. It runs everything WITHOUT placing trades and prints the summary.
6. Task Scheduler -> Create Basic Task -> "Paper trading bot" -> Weekly, Mon-Fri, 9:05 am ->
   Start a program -> `C:\nifty-desk\run_daily_bot.bat`, Start in: `C:\nifty-desk` -> Finish.
   (Keep the Kotak relay task at 9:05 am.)

If the PC was off at 9:05, run `run_daily_bot.bat` by hand before 9:15. Running it twice is safe.
Problems are shown in the window, saved in `data\bot_log.txt` and in the app - tell Claude "check the bot".

## End-of-day summary (3:45 pm)

`python daily_bot.py --eod` (or double-click `run_eod_bot.bat`) prints today's P&L and the calls still open for the next session. It does not place trades and does not change the 9:05 am run.

Schedule a second task: Weekly, Mon-Fri, 3:45 pm, program `C:\nifty-desk\run_eod_bot.bat`, Start in `C:\nifty-desk`. The same text is saved in the app under Paper trading -> "Bot summary". In the app it is also on **End of day** in the sidebar, on Desk home, and on the Track record page.

Today's P&L is what was booked on calls closed today, plus the move since the previous close on calls still open. Carried-forward MTM is the open result measured from the entry price. The forward-test book in that summary is `fwd-momentum`, together with the published idea book and any open manual journal trades.
