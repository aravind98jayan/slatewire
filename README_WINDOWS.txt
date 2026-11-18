SLATEWIRE - Windows install
===========================

Needs: Windows 10 or 11, internet, Python 3.12 or 3.13 (python.org), and the Microsoft Edge
WebView2 runtime (already on Windows 11 and on most updated Windows 10).

1. Python: if you already have Python 3.12 or 3.13 installed, skip this step (INSTALL.bat finds it).
   Otherwise install Python 3.12 from https://www.python.org/downloads/windows/
   On the first installer screen TICK "Add python.exe to PATH", then click Install Now.
   If INSTALL.bat fails on 3.13 with an error about pythonnet, install 3.12 as well, delete the
   ".venv" folder next to INSTALL.bat and run INSTALL.bat again.
2. Unzip this zip to  C:\SLATEWIRE  (not Program Files, the app saves its data next to itself).
3. In the folder you just unzipped, double-click INSTALL.bat.
   If Windows SmartScreen says "Windows protected your PC": More info -> Run anyway.
   Wait for "Done." (1-3 minutes).
4. Start the app: Desktop shortcut "SLATEWIRE", or double-click SLATEWIRE.bat.
5. If nothing opens, double-click SLATEWIRE-debug.bat and read the error in the window.

Notifications (also when the app is closed):
  Open Settings -> Notifications and switch on "Check news in the background" (INSTALL.bat also offers this
  during setup). That adds a hidden Windows task
  (settings only, no administrator needed) that checks the news every 30 minutes while you are logged in; switch
  it off again to remove the task. You get:
    "IMPORTANT"  - a new critical article appeared while the app was closed (max 4 per day, grouped)
    "Catch up"   - you have not opened SLATEWIRE for 6+ hours and new articles are waiting (max one per 6 hours)
  It does nothing while the SLATEWIRE window is open or when there is no internet, and the first check only
  remembers what exists. Settings -> "Notification pop-ups" switches the toasts on/off. If Windows or your
  organisation blocks the task, run ENABLE-NOTIFICATIONS.bat instead (it does the same thing); DISABLE-NOTIFICATIONS.bat
  removes the task.
  If no notification appears: Windows Settings -> System -> Notifications -> make sure "SLATEWIRE" is on and Focus assist is off.

Optional AI briefs: without a key the app shows page excerpts instead, in the article's original
language (briefs are what translate a non-English story into English). To add a free
Gemini key, open Settings -> AI briefs, paste it from https://aistudio.google.com/apikey and
press Save. It is stored in %APPDATA%\SLATEWIRE\secrets.json, outside the app folder, so updates
keep it. Remove deletes it; the app then falls back to a key in slatewire\config.py or the
SLATEWIRE_GEMINI_API_KEY environment variable.

Sources: click "N/M sources reachable" on the main screen to see which feeds failed.

Update later: replace the slatewire folder with the new one; your saved data lives in
slatewire\ (archive.json, settings.json, read.json) so copy those over first. A Gemini key
saved in Settings stays, because it lives in %APPDATA%\SLATEWIRE, not in the app folder.
Uninstall: UNINSTALL.bat, then delete the folder.
The app does not start with Windows.
