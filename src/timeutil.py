"""Kickoff / deadline time handling, including CET (Europe/Ljubljana) conversion."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CET = ZoneInfo("Europe/Ljubljana")


def kickoff_datetimes(date: str, local_time: str, tz: str):
    """Return (utc_dt, cet_dt) for a match given local date/time and IANA tz.

    Returns (None, None) if inputs are missing/unparseable (e.g. TBD knockout slots).
    """
    if not date or not local_time or not tz:
        return None, None
    try:
        naive = datetime.strptime(f"{date} {local_time}", "%Y-%m-%d %H:%M")
        local = naive.replace(tzinfo=ZoneInfo(tz))
        return local.astimezone(timezone.utc), local.astimezone(CET)
    except (ValueError, KeyError):
        return None, None


def fmt_cet(cet_dt) -> str:
    if cet_dt is None:
        return "TBD"
    return cet_dt.strftime("%a %d %b %H:%M CET")


def fmt_local(date: str, local_time: str, tz: str) -> str:
    if not date or not local_time:
        return "TBD"
    abbr = tz.split("/")[-1].replace("_", " ") if tz else ""
    return f"{local_time} ({abbr})"


def now_cet() -> datetime:
    return datetime.now(timezone.utc).astimezone(CET)


def countdown_str(target_cet, now=None) -> str:
    if target_cet is None:
        return "—"
    now = now or now_cet()
    delta = target_cet - now
    secs = int(delta.total_seconds())
    if secs < 0:
        return "passed"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"
