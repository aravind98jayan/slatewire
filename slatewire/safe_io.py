"""
Small shared helpers: crash-safe JSON files and size/time-limited downloads.

* write_json / write_text write to a temporary file and then swap it in, so a
  crash or power cut mid-write can never leave a half-written file behind.
* read_json moves a damaged file aside (name.corrupt-<time>) instead of
  silently treating it as empty -- otherwise the next save would overwrite
  everything that was in it. If a last-good copy (name.bak, kept by
  write_json(..., backup=True)) exists, it is restored automatically.
* LOCK serialises read-modify-write cycles on the data files (Flask answers
  requests on several threads).
* download_limited stops a feed or page that is too big or too slow.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time

import requests

LOCK = threading.RLock()


def read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError):
        try:
            os.replace(path, f"{path}.corrupt-{int(time.time())}")
        except OSError:
            pass
        # restore the last good copy if there is one (loses at most the newest save)
        bak = path + ".bak"
        try:
            with open(bak, "r", encoding="utf-8") as f:
                data = json.load(f)
            shutil.copyfile(bak, path)
            return data
        except (OSError, ValueError):
            return default
    except OSError:
        return default


def _atomic(path: str, writer) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            writer(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path: str, data, backup: bool = False, **kw) -> None:
    """Atomic write. With backup=True the previous version is first copied to path + '.bak'."""
    if backup and os.path.exists(path):
        try:
            shutil.copyfile(path, path + ".bak")
        except OSError:
            pass
    _atomic(path, lambda f: json.dump(data, f, **kw))


def write_text(path: str, text: str) -> None:
    _atomic(path, lambda f: f.write(text))


def download_limited(url: str, headers: dict, max_bytes: int, total_seconds: float,
                     connect_timeout: float = 5, read_timeout: float = 12) -> tuple[bytes, str | None]:
    """GET a http(s) URL, giving up if it is bigger than max_bytes (after
    decompression) or takes longer than total_seconds overall.
    Returns (body, declared_encoding)."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("only http(s) URLs are allowed")
    start = time.monotonic()
    with requests.get(url, headers=headers, timeout=(connect_timeout, read_timeout), stream=True) as resp:
        resp.raise_for_status()
        raw = resp.raw
        read1 = getattr(raw, "read1", None)   # returns as soon as any data arrives
        buf = bytearray()
        while True:
            chunk = read1(16384, decode_content=True) if read1 else raw.read(4096, decode_content=True)
            if not chunk:
                break
            buf += chunk
            if len(buf) > max_bytes:
                raise ValueError("response too large")
            if time.monotonic() - start > total_seconds:
                raise TimeoutError("response too slow")
        return bytes(buf), resp.encoding
