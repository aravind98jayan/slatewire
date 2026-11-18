"""
SLATEWIRE background notifier.

Runs WITHOUT a window (Windows Task Scheduler starts it every 30 minutes, only if you turned on background checks
in Settings -> Notifications, or with ENABLE-NOTIFICATIONS.bat). It never opens the app and never touches
news_data.json / history.json; it keeps its own small file (notifier_state.json). Rules:

  * app window open  -> does nothing (nothing is fetched).
  * "IMPORTANT": a NEW critical article (published in the last 24 h) appears while the app is closed -> one toast.
                  At most 4 such toasts per 24 h, several articles are grouped into one toast.
  * "Catch up":   the app has not been used for more than 6 h and there are new articles -> one toast (at most one per 6 h).
  * first run     -> only remembers what exists (no flood of toasts).
  * no internet  -> does nothing.

Command line:  notifier.py            normal check          notifier.py --test      send a test notification
               notifier.py --dry-run  show what it would do (sends nothing, saves nothing)
"""
import base64
import datetime
import json
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

import config  # noqa: E402,F401
import safe_io  # noqa: E402

try:
    import psutil
except Exception:
    psutil = None

STATE_PATH = "notifier_state.json"
APP_STATE_PATH = "app_state.json"
LOG_PATH = os.path.join("logs", "slatewire-notifier.log")
TASK_NAME = "SLATEWIRE Notifier"
CATCHUP_AFTER_SEC = 6 * 3600        # app unused for this long -> "Catch up"
CATCHUP_COOLDOWN_SEC = 6 * 3600     # and at most one Catch-up toast per this long
CRITICAL_MAX_AGE_SEC = 24 * 3600    # only fresh critical articles are "IMPORTANT"
CRITICAL_TOASTS_PER_DAY = 4
KNOWN_KEEP = 3000

PS_TOAST = r"""
$ErrorActionPreference = 'Stop'
try {
  $j = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:SLATEWIRE_TOAST)) | ConvertFrom-Json
  $id = 'SLATEWIRE'
  $k = "HKCU:\Software\Classes\AppUserModelId\$id"
  if (-not (Test-Path $k)) {
    New-Item -Path $k -Force | Out-Null
    New-ItemProperty -Path $k -Name DisplayName -Value 'SLATEWIRE' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $k -Name ShowInSettings -Value 1 -PropertyType DWord -Force | Out-Null
  }
  [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
  [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
  $t = [System.Security.SecurityElement]::Escape([string]$j.title)
  $x = [System.Security.SecurityElement]::Escape([string]$j.text)
  $xml = "<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$x</text></binding></visual></toast>"
  $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
  $doc.LoadXml($xml)
  $toast = New-Object Windows.UI.Notifications.ToastNotification $doc
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show($toast)
  exit 0
} catch { exit 2 }
"""

PS_BALLOON = r"""
$ErrorActionPreference = 'Stop'
try {
  $j = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:SLATEWIRE_TOAST)) | ConvertFrom-Json
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  $n = New-Object System.Windows.Forms.NotifyIcon
  $n.Icon = [System.Drawing.SystemIcons]::Information
  $n.Visible = $true
  $n.ShowBalloonTip(10000, [string]$j.title, [string]$j.text, [System.Windows.Forms.ToolTipIcon]::Info)
  Start-Sleep -Seconds 11
  $n.Dispose()
  exit 0
} catch { exit 3 }
"""


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 300_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + str(msg).replace("\n", " | ")[:600] + "\n")
    except OSError:
        pass


def _clean(text, limit):
    """Feed text is untrusted: drop control characters and keep it short."""
    t = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    t = " ".join(t.split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _run_ps(script, payload):
    env = dict(os.environ, SLATEWIRE_TOAST=base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii"))
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-EncodedCommand", enc]
    flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=45, creationflags=flags).returncode


def send_toast(title, text):
    """Show a Windows notification. Returns (ok, how). Tries a real toast, then a tray balloon. Not available outside Windows."""
    payload = {"title": _clean(title, 80), "text": _clean(text, 240)}
    if os.name != "nt":
        return False, "notifications need Windows (nothing shown here)"
    try:
        if _run_ps(PS_TOAST, payload) == 0:
            return True, "toast"
    except Exception as e:                                   # noqa: BLE001
        log("toast failed: %r" % (e,))
    try:
        if _run_ps(PS_BALLOON, payload) == 0:
            return True, "balloon (the normal toast was refused)"
    except Exception as e:                                   # noqa: BLE001
        log("balloon failed: %r" % (e,))
    return False, "Windows refused both notification types"


def task_status():
    """True / False = the Windows scheduled task exists / does not; None = cannot tell (not Windows)."""
    if os.name != "nt":
        return None
    try:
        return subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, timeout=15, creationflags=0x08000000).returncode == 0
    except Exception:                                        # noqa: BLE001
        return None


def _ps_quote(value: str) -> str:
    """One PowerShell single-quoted literal (install paths contain spaces)."""
    return "'" + str(value).replace("'", "''") + "'"


def _task_command() -> list[str]:
    """The PowerShell that registers the background-check task.

    Mirrors ``enable_notifications.ps1`` (kept as a manual fallback for machines
    where policy blocks the app from creating tasks) so both produce the same
    action: every 30 minutes, hidden, only while the user is logged in.
    """
    root = os.path.dirname(BASE_DIR)                 # install root, next to .venv
    pythonw = os.path.join(root, ".venv", "Scripts", "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable                    # run from source: this interpreter
    script = os.path.join(BASE_DIR, "notifier.py")
    parts = [
        "$a = New-ScheduledTaskAction -Execute %s -Argument %s -WorkingDirectory %s;"
        % (_ps_quote(pythonw), _ps_quote('"' + script + '"'), _ps_quote(BASE_DIR)),
        "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
        "-RepetitionInterval (New-TimeSpan -Minutes 30) "
        "-RepetitionDuration (New-TimeSpan -Days 3650);",
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) "
        "-MultipleInstances IgnoreNew;",
        "Register-ScheduledTask -TaskName %s -Action $a -Trigger $t -Settings $s "
        "-Description 'SLATEWIRE background news check (IMPORTANT / Catch up notifications)' "
        "-Force | Out-Null" % _ps_quote(TASK_NAME),
    ]
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", " ".join(parts)]


def _task_error(done, fallback: str) -> str:
    """The last useful line of PowerShell's output, for the Settings row."""
    lines = [ln.strip() for ln in ((done.stderr or done.stdout or "") if done else "").splitlines() if ln.strip()]
    return (lines[-1] if lines else fallback)[:200]


def task_install() -> tuple[bool, str]:
    """Create (or refresh) the background-check task. Returns (ok, message)."""
    if os.name != "nt":
        return False, "Background checks work on Windows only."
    try:
        done = subprocess.run(_task_command(), capture_output=True, text=True,
                              timeout=60, creationflags=0x08000000)
    except Exception as e:                                   # noqa: BLE001
        return False, "Could not create the background task (%s)." % e
    if done.returncode == 0 and task_status() is True:
        return True, "Background checks are on: every 30 minutes while you are logged in, nothing while this window is open."
    return False, "Windows refused to create the background task: " + _task_error(done, "the task was not created")


def task_remove() -> tuple[bool, str]:
    """Delete the background-check task. Returns (ok, message)."""
    if os.name != "nt":
        return False, "Background checks work on Windows only."
    if task_status() is False:
        return True, "Background checks are off."
    try:
        done = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                              capture_output=True, text=True, timeout=30,
                              creationflags=0x08000000)
    except Exception as e:                                   # noqa: BLE001
        return False, "Could not remove the background task (%s)." % e
    if done.returncode == 0 and task_status() is False:
        return True, "Background checks are off."
    return False, "Windows refused to remove the background task: " + _task_error(done, "the task still exists")


# --------------------------------------------------------------- decision logic (pure, unit-tested)

def app_is_running(app_state, now):
    if not app_state or not app_state.get("running"):
        return False
    pid = app_state.get("pid")
    if psutil and isinstance(pid, int):
        try:
            return psutil.pid_exists(pid)
        except Exception:                                    # noqa: BLE001
            return True
    return now - (app_state.get("last_active") or 0) < 900


def last_used(app_state):
    return max((app_state or {}).get(k) or 0 for k in ("last_active", "last_close", "last_start"))


def decide(fresh, known, state, app_state, now):
    """fresh: classified articles just downloaded; known: ids the app / notifier already know.
    Returns (toasts, new_state). toasts = list of (kind, title, text)."""
    state = dict(state or {})
    notified = set(state.get("notified", []))
    stamps = [t for t in state.get("critical_toasts", []) if now - t < 86400]
    new = [a for a in fresh if a.get("id") and a["id"] not in known and a["id"] not in notified]
    toasts = []
    crit = [a for a in new if a.get("severity") == "critical" and a.get("published_ts") and 0 <= now - a["published_ts"] <= CRITICAL_MAX_AGE_SEC]
    crit.sort(key=lambda a: a.get("published_ts", 0), reverse=True)
    if crit and len(stamps) < CRITICAL_TOASTS_PER_DAY:
        first = crit[0]
        more = len(crit) - 1
        title = "IMPORTANT - critical news" if not more else "IMPORTANT - %d critical articles" % len(crit)
        text = "%s (%s)" % (_clean(first.get("title"), 150), _clean(first.get("source"), 40)) + (" and %d more" % more if more else "")
        toasts.append(("critical", title, text))
        stamps.append(now)
        notified.update(a["id"] for a in crit)
    elif new and (now - last_used(app_state) > CATCHUP_AFTER_SEC) and (now - (state.get("last_catchup") or 0) > CATCHUP_COOLDOWN_SEC):
        n_crit = sum(1 for a in new if a.get("severity") == "critical")
        top = sorted(new, key=lambda a: ({"critical": 0, "high": 1}.get(a.get("severity"), 2), -(a.get("published_ts") or 0)))[0]
        text = "%d new articles are waiting%s. Top: %s" % (len(new), " (%d critical)" % n_crit if n_crit else "", _clean(top.get("title"), 120))
        toasts.append(("catchup", "Catch up - SLATEWIRE", text))
        state["last_catchup"] = now
    known = set(known) | {a["id"] for a in fresh if a.get("id")}
    state["known"] = list(known)[-KNOWN_KEEP:] if len(known) > KNOWN_KEEP else list(known)
    state["notified"] = list(notified)[-KNOWN_KEEP:]
    state["critical_toasts"] = stamps
    state["last_check"] = now
    return toasts, state


def _app_known_ids():
    ids = set()
    d = safe_io.read_json("history.json", {})
    if isinstance(d, dict):
        ids.update(d.keys())
    nd = safe_io.read_json("news_data.json", {})
    if isinstance(nd, dict):
        for items in (nd.get("categories") or {}).values():
            ids.update(a.get("id") for a in items if isinstance(a, dict) and a.get("id"))
    return ids


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--test" in argv:
        ok, how = send_toast("SLATEWIRE test", "If you can read this, notifications work on this PC.")
        print(("OK: " if ok else "FAILED: ") + how)
        log("test notification: %s %s" % (ok, how))
        return 0 if ok else 1
    dry = "--dry-run" in argv
    now = time.time()
    settings = safe_io.read_json("settings.json", {}) or {}
    if settings.get("notifications", True) is False:
        log("notifications are switched off in Settings - nothing to do")
        return 0
    app_state = safe_io.read_json(APP_STATE_PATH, {}) or {}
    if app_is_running(app_state, now):
        log("app window is open - skipped")
        return 0
    import fetcher
    fresh, errors, status = fetcher.collect_fresh()
    if status and all(x.get("reason") == "cannot connect" for x in status):
        log("no internet - skipped")
        return 0
    state = safe_io.read_json(STATE_PATH, {}) or {}
    known = set(state.get("known", [])) | _app_known_ids()
    if not state.get("seeded"):                               # first run: just remember what exists
        state.update(seeded=True, known=list({a["id"] for a in fresh if a.get("id")} | known)[-KNOWN_KEEP:], last_check=now)
        log("first run: remembered %d articles, no notification" % len(state["known"]))
        if not dry:
            safe_io.write_json(STATE_PATH, state)
        return 0
    toasts, new_state = decide(fresh, known, state, app_state, now)
    log("checked %d articles from %d sources (%d failed): %d notification(s)%s" % (len(fresh), len(status) - len(errors), len(errors), len(toasts), " [dry run]" if dry else ""))
    for kind, title, text in toasts:
        if dry:
            print("WOULD SHOW [%s] %s - %s" % (kind, title, text))
            continue
        ok, how = send_toast(title, text)
        log("%s toast: %s (%s) - %s" % (kind, ok, how, title))
        if not ok:                                            # not shown: do not mark as notified, try again next time
            return 1
    if not dry:
        safe_io.write_json(STATE_PATH, new_state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                                    # noqa: BLE001  never pop up an error window from a background job
        log("notifier crashed: %r" % (e,))
        sys.exit(1)
