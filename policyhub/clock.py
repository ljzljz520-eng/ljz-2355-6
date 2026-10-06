"""Injectable clock. All temporal decisions take an explicit ``at`` timestamp;
production code defaults to Clock.now(), tests freeze time."""
from __future__ import annotations

import datetime as _dt
import time as _time


def ts(text: str) -> int:
    """Parse 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM[:SS]' as a UTC unix timestamp."""
    text = text.strip()
    if len(text) == 10:
        text += " 00:00:00"
    if len(text) == 16:
        text += ":00"
    d = _dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return int(d.replace(tzinfo=_dt.timezone.utc).timestamp())


def iso(value: int | None) -> str | None:
    if value is None:
        return None
    return _dt.datetime.fromtimestamp(value, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock:
    def __init__(self, fixed: int | None = None):
        self.fixed = fixed

    def now(self) -> int:
        return int(self.fixed) if self.fixed is not None else int(_time.time())

    def freeze(self, value: int | str) -> None:
        self.fixed = ts(value) if isinstance(value, str) else int(value)
