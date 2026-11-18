"""
Per-source health history for SLATEWIRE.

The feed already reports one snapshot per refresh (reachable, empty, failed,
how many articles, how old the newest one is). That snapshot disappears as soon
as the next refresh starts, so a source that fails every other time looks fine
on every other page load. This module keeps a small rolling history instead:
attempts, successes, failure streaks, recent success rate and typical latency.

Pure module: standard library only, no Flask, no file access. ``fetcher.py``
owns the JSON file (``source_health.json``), this module owns the rules, so the
behaviour can be tested without touching the disk or the network.
"""
from __future__ import annotations

import time

RECENT_MAX = 20          # outcomes kept for the rolling success rate
MS_MAX = 10              # successful latencies kept for the average
ALERT_STREAK = 3         # this many failures in a row is worth flagging
RETIRE_AFTER = 3         # consecutive *hard* failures (404/410, DNS, bad feed) before a source is retired
STALE_DAYS = 21          # newest article older than this -> the feed is just quiet
MAX_SOURCES = 200        # hard cap on stored records
MAX_NAME = 120
MAX_REASON = 120
MAX_MS = 600_000         # ignore nonsense latencies above ten minutes

STATES = ("ok", "stale", "empty", "failing", "failed", "retired", "unknown")


def _num(value, default=0.0) -> float:
    """A finite, non-negative float (garbage becomes the default)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")) or value < 0:
        return default
    return value


def _int(value, default=0) -> int:
    return int(min(1_000_000_000_000, _num(value, default)))


def _ms(value, default=0) -> int:
    """A latency in milliseconds, capped at ten minutes."""
    return int(min(MAX_MS, _num(value, default)))


def _clean_record(record, name: str) -> dict:
    """Coerce one stored record back into the expected shape."""
    if not isinstance(record, dict):
        record = {}

    recent = []
    raw_recent = record.get("recent")
    if isinstance(raw_recent, list):
        for value in raw_recent[-RECENT_MAX:]:
            if isinstance(value, (bool, int, float)):
                recent.append(1 if value else 0)

    ms_recent = []
    raw_ms = record.get("ms_recent")
    if isinstance(raw_ms, list):
        for value in raw_ms[-MS_MAX:]:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                ms = _ms(value, -1)
                if ms > 0:
                    ms_recent.append(ms)

    state = record.get("last_state")
    if state not in ("ok", "empty", "failed"):
        state = "unknown"

    last_check = _num(record.get("last_check_ts"))
    attempts = max(len(recent), _int(record.get("attempts")))
    ok_count = min(attempts, _int(record.get("ok_count")))

    return {
        "name": name[:MAX_NAME],
        "first_seen_ts": _num(record.get("first_seen_ts"), last_check),
        "last_check_ts": last_check,
        "last_state": state,
        "last_reason": str(record.get("last_reason") or "")[:MAX_REASON],
        "last_ms": _ms(record.get("last_ms")),
        "last_items": _int(record.get("last_items")),
        "newest_ts": _int(record.get("newest_ts")),
        "last_ok_ts": _num(record.get("last_ok_ts")),
        "last_fail_ts": _num(record.get("last_fail_ts")),
        "streak_ok": _int(record.get("streak_ok")),
        "streak_fail": _int(record.get("streak_fail")),
        "hard_streak": _int(record.get("hard_streak")),
        "retired": record.get("retired") is True,
        "retired_ts": _num(record.get("retired_ts")),
        "retired_reason": str(record.get("retired_reason") or "")[:MAX_REASON],
        "just_retired": record.get("just_retired") is True,
        "country": str(record.get("country") or "")[:16],
        "lang": str(record.get("lang") or "")[:16],
        "custom": record.get("custom") is True,
        "attempts": attempts,
        "ok_count": ok_count,
        "recent": recent,
        "ms_recent": ms_recent,
        "just_alerted": record.get("just_alerted") is True,
    }


def normalize(data) -> dict[str, dict]:
    """Accept the stored JSON (flat mapping, or {"sources": {...}}) and return
    a clean ``{name: record}`` mapping. Garbage is dropped, never raised."""
    if isinstance(data, dict) and isinstance(data.get("sources"), dict):
        data = data["sources"]
    if not isinstance(data, dict):
        return {}
    out = {}
    for name, record in data.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(record, dict):
            continue
        out[name[:MAX_NAME]] = _clean_record(record, name)
    if len(out) > MAX_SOURCES:
        newest = sorted(out.items(),
                        key=lambda kv: kv[1].get("last_check_ts", 0), reverse=True)
        out = dict(newest[:MAX_SOURCES])
    return out


def apply(records, status_rows, now: float | None = None) -> dict[str, dict]:
    """Record one refresh's ``source_status`` rows and return the new history.

    A source counts as reachable unless its state is ``failed``: an empty feed
    still proves the server answered, even though it is shown as degraded.
    """
    now = time.time() if now is None else float(now)
    out = {name: dict(record) for name, record in normalize(records).items()}
    # Both flags mean "this refresh crossed the line", so they only stay true
    # for one refresh. Clearing them here (not only on a later success) also
    # covers retired sources, which are skipped and would never clear them.
    for record in out.values():
        record["just_alerted"] = False
        record["just_retired"] = False

    for row in (status_rows or []):
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        state = row.get("state")
        if not isinstance(name, str) or not name.strip() or state not in ("ok", "empty", "failed"):
            continue
        name = name[:MAX_NAME]
        reachable = state != "failed"
        record = _clean_record(out.get(name, {}), name)

        ms = _ms(row.get("ms"))
        record.update(
            last_check_ts=now,
            last_state=state,
            last_reason=str(row.get("reason") or "")[:MAX_REASON],
            last_ms=ms,
            last_items=_int(row.get("items")),
            newest_ts=_int(row.get("newest_ts")),
            attempts=record["attempts"] + 1,
            recent=(record["recent"] + [1 if reachable else 0])[-RECENT_MAX:],
        )
        if row.get("country"):
            record["country"] = str(row["country"])[:16]
        if row.get("lang"):
            record["lang"] = str(row["lang"])[:16]
        record["custom"] = row.get("custom") is True

        if reachable:
            record["last_ok_ts"] = now
            record["ok_count"] += 1
            record["streak_ok"] += 1
            record["streak_fail"] = 0
            record["hard_streak"] = 0
            record["just_alerted"] = False
            record["just_retired"] = False
            if ms > 0:
                record["ms_recent"] = (record["ms_recent"] + [ms])[-MS_MAX:]
        else:
            record["last_fail_ts"] = now
            record["streak_ok"] = 0
            record["streak_fail"] += 1
            # True only on the refresh that reaches the threshold, so a caller
            # can send exactly one notification instead of one per refresh.
            record["just_alerted"] = record["streak_fail"] == ALERT_STREAK
            # A hard failure means the URL itself is wrong (HTTP 404/410, a
            # hostname that no longer resolves, or a page that is no longer a
            # feed). Only those count toward retirement; timeouts, 403s and
            # 5xx answers are treated as temporary.
            record["hard_streak"] = record["hard_streak"] + 1 if row.get("hard") is True else 0
            if not record["retired"] and record["hard_streak"] >= RETIRE_AFTER:
                record["retired"] = True
                record["retired_ts"] = now
                record["retired_reason"] = record["last_reason"]
                record["just_retired"] = True

        record["first_seen_ts"] = record["first_seen_ts"] or now
        record["ok_count"] = min(record["ok_count"], record["attempts"])
        out[name] = record

    if len(out) > MAX_SOURCES:
        newest = sorted(out.items(),
                        key=lambda kv: kv[1].get("last_check_ts", 0), reverse=True)
        out = dict(newest[:MAX_SOURCES])
    return out


def _state(record: dict, now: float) -> str:
    if record.get("retired"):
        return "retired"
    last = record.get("last_state")
    if last == "failed":
        return "failing" if record.get("streak_fail", 0) >= 2 else "failed"
    if last == "empty":
        return "empty"
    if last == "ok":
        newest = record.get("newest_ts", 0)
        if newest and now - newest > STALE_DAYS * 86400:
            return "stale"
        return "ok"
    return "unknown"


def _success_rate(record: dict) -> int:
    recent = record.get("recent") or []
    if not recent:
        return 0
    return int(round(100.0 * sum(recent) / len(recent)))


def _avg_ms(record: dict) -> int:
    values = record.get("ms_recent") or []
    if not values:
        return 0
    return int(round(sum(values) / len(values)))


def decorate(record, now: float | None = None, current: dict | None = None) -> dict:
    """One record plus its derived health fields, for the page or the payload.

    ``current`` is this refresh's status row (when there is one); its display
    fields win over the stored copy, but the streak/success numbers keep coming
    from the history.
    """
    now = time.time() if now is None else float(now)
    record = _clean_record(record, str((record or {}).get("name") or ""))

    row = current if isinstance(current, dict) else None
    if row and row.get("state") in ("ok", "empty", "failed"):
        record.update(
            last_state=row["state"],
            last_reason=str(row.get("reason") or "")[:MAX_REASON],
            last_ms=_ms(row.get("ms")),
            last_items=_int(row.get("items")),
            newest_ts=_int(row.get("newest_ts")),
        )

    return {
        "name": record["name"],
        "state": _state(record, now),
        "success_rate": _success_rate(record),
        "attempts": record["attempts"],
        "ok_count": record["ok_count"],
        "fail_streak": record["streak_fail"],
        "ok_streak": record["streak_ok"],
        "last_ok_ts": record["last_ok_ts"],
        "last_fail_ts": record["last_fail_ts"],
        "last_check_ts": record["last_check_ts"],
        "last_ms": record["last_ms"],
        "avg_ms": _avg_ms(record),
        "last_items": record["last_items"],
        "newest_ts": record["newest_ts"],
        "last_reason": record["last_reason"],
        "alert": record["streak_fail"] >= ALERT_STREAK,
        "just_alerted": record["just_alerted"],
        "retired": record["retired"],
        "retired_ts": record["retired_ts"],
        "retired_reason": record["retired_reason"],
        "hard_streak": record["hard_streak"],
        "country": record["country"],
        "lang": record["lang"],
        "custom": record["custom"],
        "just_retired": record["just_retired"],
        "recent": list(record["recent"]),
    }


def view(records, status_rows=None, now: float | None = None) -> list[dict]:
    """The page-facing list: stored history, in source order, with any
    never-seen source from the current refresh added once (not persisted)."""
    now = time.time() if now is None else float(now)
    history = normalize(records)
    rowmap = {}
    order = []
    for row in (status_rows or []):
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name.strip() or name in rowmap:
            continue
        name = name[:MAX_NAME]
        rowmap[name] = row
        order.append(name)
        if name not in history:
            history = apply(history, [row], now)   # local only
    order.extend(sorted(name for name in history if name not in rowmap))
    return [decorate(history[name], now, rowmap.get(name))
            for name in order if name in history]


def retired_names(records) -> set[str]:
    """The names currently retired (their feeds are skipped until revived)."""
    return {name for name, record in normalize(records).items() if record.get("retired")}


def revive(records, name: str) -> dict[str, dict]:
    """Clear a retirement and its hard-failure count so the next refresh tries
    the source again. Normal history (attempts, success rate) is kept."""
    out = {key: dict(record) for key, record in normalize(records).items()}
    record = out.get(str(name)[:MAX_NAME])
    if record is None:
        return out
    record.update(
        retired=False, retired_ts=0.0, retired_reason="", hard_streak=0,
        streak_fail=0, just_alerted=False, just_retired=False,
        last_state="unknown", last_reason="",
    )
    out[record["name"]] = record
    return out


def summarize(rows) -> dict:
    """Counts for the Sources panel header."""
    counts = {state: 0 for state in STATES}
    alerts = 0
    last_check = 0.0
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        state = row.get("state")
        counts[state if state in counts else "unknown"] += 1
        if row.get("alert"):
            alerts += 1
        last_check = max(last_check, _num(row.get("last_check_ts")))
    counts["sources"] = sum(counts.values())
    counts["unhealthy"] = counts["failing"] + counts["failed"] + counts["empty"] + counts["stale"]
    counts["alerts"] = alerts
    counts["last_check_ts"] = int(last_check)
    return counts
