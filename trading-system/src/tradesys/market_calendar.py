"""NYSE sessions and the 9:30-11:00 trading window, in America/New_York time.

Holidays, early closes and one-off closures (Hurricane Sandy 2012, national days of
mourning 2018 and 2025, ...) come from `exchange_calendars` (XNYS), not a hand-kept list.
Daylight saving time is handled by zoneinfo: never hardcode EST/EDT offsets.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xc
import pandas as pd

from .config import SessionCfg

NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc


@lru_cache(maxsize=1)
def _cal():
    return xc.get_calendar("XNYS", start="2005-01-01", end="2027-12-31")


def is_session(day: dt.date) -> bool:
    return bool(_cal().is_session(pd.Timestamp(day)))


def sessions(start: dt.date, end: dt.date) -> list[dt.date]:
    """Trading days from start to end inclusive."""
    s = _cal().sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    return [d.date() for d in s]


def previous_session(day: dt.date) -> dt.date:
    return _cal().previous_session(pd.Timestamp(day)).date()


def session_close(day: dt.date) -> dt.time:
    """Regular close in New York time: 16:00, or 13:00 on early-close days."""
    c = _cal().session_close(pd.Timestamp(day)).tz_convert(NY)
    return c.time()


def is_early_close(day: dt.date) -> bool:
    return session_close(day) < dt.time(16, 0)


@dataclass(frozen=True)
class Window:
    """One session's trading window as tz-aware timestamps (New York)."""
    day: dt.date
    open: pd.Timestamp
    first_entry: pd.Timestamp
    entry_cutoff: pd.Timestamp
    flatten_start: pd.Timestamp
    hard_flat: pd.Timestamp
    close: pd.Timestamp
    premarket_start: pd.Timestamp


def window(day: dt.date, cfg: SessionCfg) -> Window:
    if not is_session(day):
        raise ValueError(f"{day} is not an NYSE session")
    at = lambda t: pd.Timestamp(dt.datetime.combine(day, t), tz=NY)  # noqa: E731
    close = at(session_close(day))
    hard = min(at(cfg.hard_flat), close)
    return Window(day=day, open=at(cfg.open), first_entry=at(cfg.first_entry),
                  entry_cutoff=min(at(cfg.entry_cutoff), hard), flatten_start=min(at(cfg.flatten_start), hard),
                  hard_flat=hard, close=close, premarket_start=at(cfg.premarket_start))


def to_ny(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if index.tz is None:
        raise ValueError("timestamps must be tz-aware (UTC from the vendor)")
    return index.tz_convert(NY)
