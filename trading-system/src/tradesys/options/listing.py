"""Which SPY expirations existed on a given day (approximate history, secondary sources).

Monthly (third Friday) and Friday weeklies throughout 2010+ (weeklies from 2010-06-11);
Wednesday expirations from 2016-08-31; Monday from 2018-02-26; Tuesday and Thursday from
2022-11-15 / 2022-11-17 (Cboe table cited by an NYU Stern study, and Interactive Brokers notes).
So true daily 0DTE exists only from mid-November 2022. A Friday expiry that falls on a holiday
moves to the previous session; a Monday-Thursday expiry on a holiday is not listed.
These dates should be replaced by OPRA instrument definitions when paid data is available.
"""
from __future__ import annotations

import datetime as dt

from ..market_calendar import is_session, previous_session

FIRST = {4: dt.date(2010, 6, 11), 2: dt.date(2016, 8, 31), 0: dt.date(2018, 2, 26), 1: dt.date(2022, 11, 15),
         3: dt.date(2022, 11, 17)}


def _third_friday(y: int, m: int) -> dt.date:
    d = dt.date(y, m, 15)
    return d + dt.timedelta(days=(4 - d.weekday()) % 7)


def expirations(day: dt.date, horizon_days: int = 10) -> list[dt.date]:
    out = set()
    for i in range(horizon_days + 1):
        d = day + dt.timedelta(days=i)
        wd = d.weekday()
        listed = (wd in FIRST and d >= FIRST[wd]) or d == _third_friday(d.year, d.month)
        if not listed:
            continue
        if is_session(d):
            out.add(d)
        elif wd == 4 or d == _third_friday(d.year, d.month):
            p = previous_session(d)
            if p >= day:
                out.add(p)
    return sorted(out)


def nearest(day: dt.date, min_dte: int = 0) -> dt.date | None:
    for e in expirations(day, 14):
        if (e - day).days >= min_dte:
            return e
    return None
