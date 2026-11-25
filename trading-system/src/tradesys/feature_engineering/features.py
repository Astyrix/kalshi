"""Causal features for 1-minute bars.

Convention: a bar is labelled by its start time; the row for bar t holds what is known at
the CLOSE of bar t (t + 1 minute). A strategy that reads row t can act no earlier than the
open of bar t + 1. Every feature here passes the truncation test in tests/test_features.py:
computing on data cut at bar t gives the same value at t as computing on the full history.

Two tables:
  daily_context(bars)  one row per session, known before 9:30 (prior day, pre-market, daily ATR)
  intraday(bars)       one row per regular-session bar (VWAP, EMA, RSI, MACD, ATR, bands,
                       relative volume, opening ranges, session high/low so far, ...)
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..market_calendar import NY, session_close

RTH_OPEN = dt.time(9, 30)


def _ny(bars: pd.DataFrame) -> pd.DataFrame:
    b = bars.copy()
    b.index = b.index.tz_convert(NY)
    b["day"] = b.index.date
    b["tod"] = b.index.time
    return b


def regular_session(bars: pd.DataFrame) -> pd.DataFrame:
    """Bars from 9:30 to each session's close (early closes respected), New York index."""
    b = _ny(bars)
    closes = {d: session_close(d) for d in pd.unique(b["day"])}
    keep = (b["tod"] >= RTH_OPEN) & np.array([t < closes[d] for d, t in zip(b["day"], b["tod"])])
    return b[keep]


def wilder(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up, dn = wilder(d.clip(lower=0), n), wilder((-d).clip(lower=0), n)
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def daily_context(bars: pd.DataFrame, dividends: pd.Series | None = None, vix: pd.Series | None = None) -> pd.DataFrame:
    """One row per session with values fixed before the open.

    dividends: optional Series of cash amounts indexed by ex-date; on an ex-date SPY opens
    lower by about the dividend, so `gap_adj` adds it back."""
    b = _ny(bars)
    rth = regular_session(bars)
    g = rth.groupby("day")
    d = pd.DataFrame({"rth_open": g["open"].first(), "rth_high": g["high"].max(), "rth_low": g["low"].min(),
                      "rth_close": g["close"].last(), "rth_volume": g["volume"].sum()})
    d.index = pd.Index(d.index, name="day")
    pre = b[(b["tod"] >= dt.time(4, 0)) & (b["tod"] < RTH_OPEN)].groupby("day")
    d["pm_high"], d["pm_low"] = pre["high"].max(), pre["low"].min()
    d["pm_volume"], d["pm_last"] = pre["volume"].sum(), pre["close"].last()
    d["prev_close"], d["prev_high"], d["prev_low"] = d["rth_close"].shift(1), d["rth_high"].shift(1), d["rth_low"].shift(1)
    div = dividends.reindex(d.index).fillna(0.0) if dividends is not None else 0.0
    d["gap"] = d["rth_open"] / d["prev_close"] - 1
    d["gap_adj"] = (d["rth_open"] + div) / d["prev_close"] - 1
    tr = true_range(d["rth_high"], d["rth_low"], d["rth_close"])
    d["atr_d14"] = wilder(tr, 14).shift(1)                     # known before today's open
    lr = np.log(d["rth_close"]).diff()
    d["rv_d20"] = lr.rolling(20).std().shift(1) * np.sqrt(252)
    d["ret_d1"] = d["rth_close"].shift(1) / d["rth_close"].shift(2) - 1
    d["ret_d5"] = d["rth_close"].shift(1) / d["rth_close"].shift(6) - 1
    d["sma_d20_dist"] = d["prev_close"] / d["rth_close"].rolling(20).mean().shift(1) - 1
    rng = d["rth_high"] - d["rth_low"]
    d["prev_range"] = rng.shift(1)
    d["nr4"] = (rng == rng.rolling(4).min()).shift(1).astype("boolean")   # yesterday narrowest of the last 4
    d["nr7"] = (rng == rng.rolling(7).min()).shift(1).astype("boolean")
    d["low20"] = d["rth_low"].rolling(20).min().shift(1)                   # prior 20 sessions, known before the open
    d["high20"] = d["rth_high"].rolling(20).max().shift(1)
    # calendar facts are published in advance (exchange calendar), so they are not lookahead
    days = list(d.index)
    nxt = {a: b for a, b in zip(days, days[1:])}
    try:
        from ..market_calendar import sessions as _sessions
        cal = _sessions(days[0], days[-1] + dt.timedelta(days=10))
        nxt = {a: b for a, b in zip(cal, cal[1:])}
        prv = {b: a for a, b in zip(cal, cal[1:])}
    except Exception:
        prv = {b: a for a, b in zip(days, days[1:])}
    def _gapdays(x):
        n = nxt.get(x)
        return (n - x).days if n else 1
    d["pre_holiday"] = [_gapdays(x) > (3 if x.weekday() == 4 else 1) for x in d.index]
    # turn of month: the last session of a month or the first three of the next
    first3, last1 = set(), set()
    if days:
        cal_days = sorted(set(list(nxt) + list(nxt.values())))
        by_month: dict = {}
        for x in cal_days:
            by_month.setdefault((x.year, x.month), []).append(x)
        for v in by_month.values():
            first3.update(v[:3])
            last1.add(v[-1])
    d["tom"] = [x in first3 or x in last1 for x in d.index]
    d["dow"] = [x.weekday() for x in d.index]
    if vix is not None:                                   # VIX close of the previous session
        v = pd.Series(np.asarray(vix, dtype=float), index=[pd.Timestamp(i).date() for i in vix.index]).sort_index()
        keys = np.array(v.index, dtype=object)
        pos = np.searchsorted(keys, np.array(list(d.index), dtype=object), side="left") - 1
        vals = v.to_numpy()
        d["vix_prev"] = [vals[i] if i >= 0 else np.nan for i in pos]
    d["early_close"] = [session_close(x) < dt.time(16, 0) for x in d.index]
    # same-day values (rth_*) are outcomes, not features: keep them under explicit names only
    return d.rename(columns={"rth_high": "day_high_outcome", "rth_low": "day_low_outcome",
                             "rth_close": "day_close_outcome", "rth_volume": "day_volume_outcome"})


def intraday(bars: pd.DataFrame, or_minutes: tuple[int, ...] = (5, 15, 30), relvol_days: int = 20) -> pd.DataFrame:
    """Per-bar causal features over the regular session."""
    r = regular_session(bars).copy()
    if r.empty:
        return r
    o, h, l, c, v = r["open"], r["high"], r["low"], r["close"], r["volume"].astype(float)
    day = r["day"]
    r["minute"] = (r.index.hour - 9) * 60 + r.index.minute - 30          # 0 = the 9:30 bar
    # continuous indicators over the regular-session series (warm-up from earlier sessions)
    r["ema9"], r["ema20"] = c.ewm(span=9, adjust=False).mean(), c.ewm(span=20, adjust=False).mean()
    r["sma20"] = c.rolling(20).mean()
    sd20 = c.rolling(20).std()
    r["bb_up"], r["bb_dn"] = r["sma20"] + 2 * sd20, r["sma20"] - 2 * sd20
    r["rsi14"] = rsi(c, 14)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    r["macd"], r["macd_sig"] = macd, macd.ewm(span=9, adjust=False).mean()
    tr = true_range(h, l, c)
    tr[r["minute"] == 0] = (h - l)[r["minute"] == 0]                    # no overnight gap in 1-minute ATR
    r["atr14"] = wilder(tr, 14)
    lr = np.log(c).diff()
    lr[r["minute"] == 0] = np.nan
    r["rv30"] = lr.rolling(30, min_periods=20).std()
    r["roc5"], r["roc15"] = c / c.shift(5) - 1, c / c.shift(15) - 1
    # session-anchored values
    tp = (h + l + c) / 3
    r["vwap"] = (tp * v).groupby(day).cumsum() / v.groupby(day).cumsum().replace(0, np.nan)
    r["sess_high"], r["sess_low"] = h.groupby(day).cummax(), l.groupby(day).cummin()
    r["sess_open"] = o.groupby(day).transform("first")
    r["cum_vol"] = v.groupby(day).cumsum()
    # noise area (Zarattini, Aziz, Barbon): average absolute move from the open at this minute
    # of the session over the previous 14 sessions
    r["ret_open"] = c / r["sess_open"] - 1
    mv = r["ret_open"].abs().groupby([day, r["minute"]]).mean().unstack("minute")
    noise = mv.rolling(14, min_periods=10).mean().shift(1)
    r["noise_sigma"] = noise.stack().reindex(pd.MultiIndex.from_arrays([day, r["minute"]])).to_numpy()
    for n in or_minutes:
        first = r["minute"] < n
        orh = h.where(first).groupby(day).transform("max")
        orl = l.where(first).groupby(day).transform("min")
        known = r["minute"] >= n - 1                                   # set at the close of bar n-1
        r[f"or{n}_high"], r[f"or{n}_low"] = orh.where(known), orl.where(known)
    # relative volume: this minute's volume vs the same minute over the previous `relvol_days` sessions
    piv = v.groupby([day, r["minute"]]).sum().unstack("minute")
    base = piv.rolling(relvol_days, min_periods=5).mean().shift(1)
    key = pd.MultiIndex.from_arrays([day, r["minute"]])
    r["relvol"] = v.to_numpy() / base.stack().reindex(key).to_numpy()
    cum_base = base.cumsum(axis=1).stack().reindex(key).to_numpy()
    r["cum_relvol"] = r["cum_vol"].to_numpy() / cum_base
    return r.drop(columns=["tod"])
