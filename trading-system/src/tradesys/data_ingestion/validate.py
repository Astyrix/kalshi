"""Data-quality checks for 1-minute bars, and the markdown data-quality report.

Checks per regular session (9:30 to the close, early closes respected):
missing minutes, duplicate timestamps, OHLC inconsistencies, non-positive or NaN prices,
zero-volume minutes, extreme 1-minute moves (robust z-score), bars outside any session,
and whole sessions with no data. Nothing is "fixed" silently: the report says what is wrong
and the loader's callers decide (the backtester skips sessions that fail).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..market_calendar import NY, session_close, sessions


@dataclass
class SessionQuality:
    day: dt.date
    expected: int
    present: int
    missing_first_90: int          # missing bars in 9:30-11:00, the trading window
    zero_volume: int
    bad_ohlc: int
    extreme_moves: int

    @property
    def usable(self) -> bool:
        return self.present > 0 and self.missing_first_90 <= 3 and self.bad_ohlc == 0


@dataclass
class QualityReport:
    start: dt.date
    end: dt.date
    sessions: list[SessionQuality] = field(default_factory=list)
    duplicates: int = 0
    nonpositive_or_nan: int = 0
    outside_sessions: int = 0
    empty_sessions: list[dt.date] = field(default_factory=list)

    def summary(self) -> dict:
        s = self.sessions
        return {
            "sessions_expected": len(s), "sessions_empty": len(self.empty_sessions),
            "sessions_usable": sum(q.usable for q in s),
            "bars_present": sum(q.present for q in s), "bars_expected": sum(q.expected for q in s),
            "missing_in_9_30_11_00": sum(q.missing_first_90 for q in s),
            "zero_volume_bars": sum(q.zero_volume for q in s), "bad_ohlc_bars": sum(q.bad_ohlc for q in s),
            "extreme_moves": sum(q.extreme_moves for q in s), "duplicates": self.duplicates,
            "nonpositive_or_nan": self.nonpositive_or_nan, "bars_outside_sessions": self.outside_sessions,
        }


def check(bars: pd.DataFrame, start: dt.date, end: dt.date, z_extreme: float = 12.0) -> QualityReport:
    rep = QualityReport(start, end)
    rep.duplicates = int(bars.index.duplicated().sum())
    b = bars[~bars.index.duplicated(keep="last")].sort_index()
    px = b[["open", "high", "low", "close"]]
    rep.nonpositive_or_nan = int((px.isna() | (px <= 0)).any(axis=1).sum())
    ny = b.index.tz_convert(NY)
    days = pd.Index(ny.date)
    ok_days = set(sessions(start, end))
    rep.outside_sessions = int(sum(1 for d in days if d not in ok_days))
    by_day = {d: g for d, g in b.groupby(days)}
    ret = np.log(b["close"]).diff()
    med = ret.rolling(2000, min_periods=200).median()
    mad = (ret - med).abs().rolling(2000, min_periods=200).median() * 1.4826
    z = ((ret - med) / mad.replace(0, np.nan)).abs()
    for d in sorted(ok_days):
        o = pd.Timestamp(dt.datetime.combine(d, dt.time(9, 30)), tz=NY)
        c = pd.Timestamp(dt.datetime.combine(d, session_close(d)), tz=NY)
        expected = int((c - o).total_seconds() // 60)
        g = by_day.get(d)
        if g is None:
            rep.sessions.append(SessionQuality(d, expected, 0, 90, 0, 0, 0))
            rep.empty_sessions.append(d)
            continue
        gi = g.index.tz_convert(NY)
        rth = g[(gi >= o) & (gi < c)]
        ri = rth.index.tz_convert(NY)
        win = ri[(ri < o + pd.Timedelta(minutes=90))]
        bad = ((rth["high"] < rth[["open", "close"]].max(axis=1)) | (rth["low"] > rth[["open", "close"]].min(axis=1))
               | (rth["low"] > rth["high"])).sum()
        rep.sessions.append(SessionQuality(
            day=d, expected=expected, present=len(rth), missing_first_90=90 - len(win),
            zero_volume=int((rth["volume"] <= 0).sum()), bad_ohlc=int(bad),
            extreme_moves=int((z.reindex(rth.index) > z_extreme).sum())))
    return rep


def to_markdown(rep: QualityReport, title: str, notes: list[str] | None = None) -> str:
    s = rep.summary()
    lines = [f"# {title}", "", f"Period {rep.start} to {rep.end}. Generated "
             f"{pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC.", "", "| check | value |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {v:,} |" for k, v in s.items()]
    worst = sorted(rep.sessions, key=lambda q: -q.missing_first_90)[:15]
    lines += ["", "## Sessions with the most missing bars in 9:30-11:00", "",
              "| day | missing (of 90) | bars present / expected | zero-volume | bad OHLC | extreme moves |", "|---|---|---|---|---|---|"]
    lines += [f"| {q.day} | {q.missing_first_90} | {q.present} / {q.expected} | {q.zero_volume} | {q.bad_ohlc} | "
              f"{q.extreme_moves} |" for q in worst if q.missing_first_90 > 0]
    if rep.empty_sessions:
        lines += ["", f"## Sessions with no data ({len(rep.empty_sessions)})", "",
                  ", ".join(str(d) for d in rep.empty_sessions[:200])]
    if notes:
        lines += ["", "## Notes and limitations", ""] + [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"
