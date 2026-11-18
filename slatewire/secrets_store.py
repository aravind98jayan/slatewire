"""
The optional Gemini API key, stored outside the app folder.

The key belongs to the user, not to the app: saving it in
``%APPDATA%\\SLATEWIRE\\secrets.json`` means replacing the app folder during an
update cannot erase it. A key saved here wins over ``config.GEMINI_API_KEY``
(which also covers the ``SLATEWIRE_GEMINI_API_KEY`` environment variable).

The key is never sent back to the page: the Settings panel only learns whether
one is configured and where it comes from.
"""
from __future__ import annotations

import os
import re

import config
import safe_io

APP_FOLDER = "SLATEWIRE"
FILE_NAME = "secrets.json"
FIELD = "gemini_api_key"
ENV_VAR = "SLATEWIRE_GEMINI_API_KEY"
PLACEHOLDER = "paste-your-key-here"

MIN_KEY_LEN = 20
MAX_KEY_LEN = 512
_KEY_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def data_dir() -> str:
    """The per-user folder for secrets.

    Windows: ``%APPDATA%\\SLATEWIRE``. Linux/macOS: ``$XDG_CONFIG_HOME/SLATEWIRE``
    (``~/.config/SLATEWIRE`` when that variable is unset).
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = appdata
    elif os.name == "nt":
        base = os.path.expanduser("~")
    else:
        base = (os.environ.get("XDG_CONFIG_HOME")
                or os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, APP_FOLDER)


def path() -> str:
    return os.path.join(data_dir(), FILE_NAME)


def _read_file() -> dict:
    data = safe_io.read_json(path(), {})
    return data if isinstance(data, dict) else {}


def _clean(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def validate(key) -> str:
    """An error message for an unusable key, or '' when it looks valid.

    Deliberately loose: Google has shipped ``AIza...`` and ``AQ.`` keys, so the
    check is only shape (length, no whitespace) rather than a fixed prefix.
    """
    key = _clean(key)
    if not key:
        return "Paste a Gemini API key first."
    if len(key) < MIN_KEY_LEN:
        return "That key is too short. Copy the whole key from Google AI Studio."
    if len(key) > MAX_KEY_LEN:
        return "That key is too long to be a Gemini API key."
    if not _KEY_RE.match(key):
        return "That does not look like a Gemini API key (letters, digits, dots, dashes and underscores only)."
    return ""


def saved_key() -> str:
    """The key saved in Settings, or '' when there is none (or it is malformed)."""
    key = _clean(_read_file().get(FIELD))
    return key if key and not validate(key) else ""


def config_key() -> str:
    """The key from config.py / the environment variable, or '' when unset."""
    key = _clean(getattr(config, "GEMINI_API_KEY", ""))
    return "" if key in ("", PLACEHOLDER) else key


def effective_key() -> str:
    """The key the app should use: Settings first, then config.py."""
    return saved_key() or config_key()


def has_key() -> bool:
    return bool(effective_key())


def source() -> str:
    """Where the working key comes from: 'settings', 'config' or 'none'."""
    if saved_key():
        return "settings"
    return "config" if config_key() else "none"


def status() -> dict:
    """What the Settings panel may know -- never the key itself."""
    src = source()
    return {"configured": src != "none", "source": src}


def save_key(key) -> str | None:
    """Store a key. Returns None on success, else a message for the user."""
    key = _clean(key)
    problem = validate(key)
    if problem:
        return problem
    data = _read_file()
    data[FIELD] = key
    try:
        os.makedirs(data_dir(), exist_ok=True)
        safe_io.write_json(path(), data, indent=2)
        try:
            os.chmod(path(), 0o600)   # best effort; NTFS already restricts %APPDATA%
        except OSError:
            pass
    except (OSError, TypeError):
        return "Could not save the key. Check that %APPDATA% is writable."
    return None


def clear_key() -> bool:
    """Forget the saved key (config.py is untouched). True when one was there."""
    data = _read_file()
    if FIELD not in data:
        return False
    data.pop(FIELD, None)
    try:
        if data:
            safe_io.write_json(path(), data, indent=2)
        else:
            os.remove(path())
    except OSError:
        return False
    return True
