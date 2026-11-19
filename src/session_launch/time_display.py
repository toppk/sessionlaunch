"""One human-readable timestamp format for SessionLaunch views."""

from datetime import datetime
from zoneinfo import ZoneInfo


LOCAL_ZONE = ZoneInfo("America/New_York")


def format_datetime(value, now=None):
    if not value:
        return "Unknown"
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=LOCAL_ZONE)
    observed = observed.astimezone(LOCAL_ZONE)
    current = (now or datetime.now(LOCAL_ZONE)).astimezone(LOCAL_ZONE)
    seconds = (current - observed).total_seconds()
    future = seconds < -30
    seconds = abs(seconds)
    if seconds < 60:
        relative = "just now"
    else:
        for unit, length in (("year", 31557600), ("month", 2629800), ("week", 604800),
                             ("day", 86400), ("hour", 3600), ("minute", 60)):
            if seconds >= length:
                count = int(seconds // length)
                amount = f"{count} {unit}{'' if count == 1 else 's'}"
                relative = f"in {amount}" if future else f"{amount} ago"
                break
    return f"{observed:%Y-%m-%d %H:%M} {observed.tzname()} ({relative})"
