"""Time source in one place, so "today" is the user's local day and tests can fake it."""
from __future__ import annotations

from datetime import date, datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Clock:
    def __init__(self, tz_name: str = "Europe/Moscow"):
        try:
            self.tz: tzinfo = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:  # tzdata missing (e.g. minimal Windows) -> UTC
            self.tz = timezone.utc

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()
