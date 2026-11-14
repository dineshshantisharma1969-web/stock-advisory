# Kotak live prices on your office PC

This small program runs on a Windows PC in India from 9:14 am to 3:32 pm on weekdays. Every minute it fetches
Kotak's latest prices for the app's stocks and saves them, so the app (on your phone too) shows live P&L.
It only READS prices with your Kotak consumer key. It never logs in to your account and cannot place orders.

## One-time setup (about 30 minutes)

**1. Install Python** (skip if already installed)
- Go to python.org -> Downloads -> Windows -> download the latest Python 3 installer.
- Run it. On the first screen TICK "Add python.exe to PATH", then click Install Now.

**2. Put the program on the PC**
- Open https://github.com/dineshshantisharma1969-web/stock-advisory/tree/nifty500-desk
- Green "Code" button -> Download ZIP. Unzip it.
- Copy the `relay` folder to `C:\kotak-relay`.

**3. Choose a passphrase and set it in the app**
- Make up a long phrase (12+ characters), e.g. a short sentence only you know.
- In the app: Desk home -> "Kotak live prices" -> type it in "Set relay passphrase" -> Save.

**4. Fill in the settings file**
- In `C:\kotak-relay`, copy `relay_settings_TEMPLATE.ini` and rename the copy to `relay_settings.ini`.
- Open it with Notepad. Paste your Kotak consumer key and the same passphrase. Save.
- Never send this file or a photo of it to anyone (including in chat).

**5. Test**
- Double-click `test_relay.bat`. After 1-2 minutes it should say "Test round: saved 500 prices" (roughly).
- In the app: Desk home -> "Kotak live prices" shows the time of the last update.

**6. Start it automatically every weekday**
- Start menu -> type "Task Scheduler" -> open it -> "Create Basic Task".
- Name: Kotak relay. Trigger: Weekly, Monday to Friday, 9:05 am.
- Action: Start a program -> Browse -> `C:\kotak-relay\start_relay.bat`. Finish.
- The PC must be ON and not asleep during market hours (Settings -> System -> Power -> Sleep: Never).

## If something goes wrong
- The window and `relay_log.txt` (same folder) say what happened, in plain words.
- "did not accept the consumer key": generate a new key in Kotak Neo -> More -> Trade API, paste it again.
- "did not accept the relay passphrase": set the same phrase again in the app and in the settings file.
- Tell Claude "check the relay" - the app records each update, so Claude can see if it is working.
