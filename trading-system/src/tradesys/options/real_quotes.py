"""Paper options results priced from REAL recorded SPY option quotes (Yahoo, ~15 min delayed).

Rules below were declared on 2026-11-25 BEFORE any 9:30-11:00 quotes had been recorded, and
must not be tuned on the recorded days (add new variants as new names instead and say when).

  long_0dte / long_1dte   every shadow share trade as an at-the-money call (long) or put (short)
                          on the nearest expiry with >= 0 / >= 1 day left; 50% premium stop on the mid.
  credit_2w               every shadow share trade as a $2-wide credit spread on the 0DTE expiry:
                          long -> sell the first put at/below spot, buy the put $2 lower;
                          short -> sell the first call at/above spot, buy the call $2 higher.
                          Stop when the spread's mid cost to close reaches 2x the credit.
  condor_{0945,1000}_{30,60}  no signal: 0DTE iron condor entered at 9:45 / 10:00, short strikes
                          the first listed strikes beyond spot x (1 +- 0.30% / 0.60%), wings $3
                          further out; stop at 2x credit (mid); exit 10:55.
  All exits by the share trade's exit (<= 10:55) or 10:55, inside the 9:30-11:00 window.

Fills: every leg crosses the spread (buy at the ask, sell at the bid) on the first quote stamped
at or after the decision time (never an older quote: that would let a momentum signal buy a
stale, cheaper option). Mid-price P&L is reported alongside for reference.
Costs: $0.69 per contract per leg per side. Sizing: max loss per trade within
equity x risk_per_trade (premium x stop for long options, width - credit for spreads).
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from ..market_calendar import NY

COMMISSION = 0.69
MAX_AGE_S = 150          # a quote more than this after the decision time counts as missing


class Quotes:
    def __init__(self, df: pd.DataFrame):
        d = df.dropna(subset=["quote_ts"]).copy()
        d = d.sort_values("fetch_ts").drop_duplicates(["quote_ts", "expiry", "right", "strike"], keep="last")
        self.df = d
        self.times = pd.DatetimeIndex(sorted(d["quote_ts"].unique()))
        self.by = {k: g.set_index("quote_ts").sort_index() for k, g in d.groupby(["expiry", "right", "strike"])}

    def expiries(self) -> list[dt.date]:
        return sorted(self.df["expiry"].unique())

    def strikes(self, expiry: dt.date, right: str) -> np.ndarray:
        x = self.df[(self.df["expiry"] == expiry) & (self.df["right"] == right)]["strike"]
        return np.sort(x.unique())

    def at(self, t: pd.Timestamp, expiry, right, strike, exact: bool = False):
        """First quote stamped at or after t: (bid, ask, quote_ts), or None if missing / too late / one-sided."""
        g = self.by.get((expiry, right, float(strike)))
        if g is None:
            return None
        i = g.index.searchsorted(t, side="left")
        if i >= len(g):
            return None
        q = g.index[i]
        if (exact and q != t) or (q - t).total_seconds() > MAX_AGE_S:
            return None
        b, a = float(g["bid"].iloc[i]), float(g["ask"].iloc[i])
        if not (np.isfinite(a) and a > 0 and np.isfinite(b) and b >= 0 and a >= b):
            return None
        return b, a, q


def _run(Q: Quotes, legs: list[tuple], t0: pd.Timestamp, t1: pd.Timestamp, stop) -> dict | None:
    """legs: [(expiry, right, strike, +1 long / -1 short)]. Per one unit (x100 shares).
    stop(value_mid, entry) -> bool, checked on every snapshot strictly between entry and t1."""
    e = [Q.at(t0, *l[:3]) for l in legs]
    if any(x is None for x in e):
        return {"skip": "no quote at entry"}
    q0 = max(x[2] for x in e)
    if any(x[2] != q0 for x in e):                       # all legs from one snapshot
        e = [Q.at(q0, *l[:3], exact=True) for l in legs]
        if any(x is None for x in e):
            return {"skip": "legs not in one snapshot"}
    cash0 = sum((x[0] if l[3] < 0 else -x[1]) for l, x in zip(legs, e))      # natural: +credit / -debit
    mid0 = sum(l[3] * (x[0] + x[1]) / 2 for l, x in zip(legs, e))
    ex, why = None, "rule exit"
    for qt in Q.times[(Q.times > q0) & (Q.times < t1)]:
        m = [Q.at(qt, *l[:3], exact=True) for l in legs]
        if any(x is None for x in m):
            continue
        v = sum(l[3] * (x[0] + x[1]) / 2 for l, x in zip(legs, m))
        if stop(v, {"cash0": cash0, "mid0": mid0}):
            ex, why = m, "stop"
            break
    if ex is None:
        ex = [Q.at(t1, *l[:3]) for l in legs]
        if any(x is None for x in ex):
            return {"skip": "no quote at exit", "entry_q": str(q0)}
        q1 = max(x[2] for x in ex)
        if any(x[2] != q1 for x in ex):
            ex = [Q.at(q1, *l[:3], exact=True) for l in legs]
            if any(x is None for x in ex):
                return {"skip": "exit legs not in one snapshot"}
    cash1 = sum((-x[1] if l[3] < 0 else x[0]) for l, x in zip(legs, ex))
    mid1 = sum(l[3] * (x[0] + x[1]) / 2 for l, x in zip(legs, ex))
    return {"entry_q": e[0][2], "exit_q": ex[0][2], "cash0": cash0, "cash1": cash1, "mid0": mid0, "mid1": mid1,
            "exit_reason": why, "legs": [{"expiry": str(l[0]), "right": l[1], "strike": l[2], "qty": l[3],
                                          "entry_bid": x[0], "entry_ask": x[1], "exit_bid": y[0], "exit_ask": y[1]}
                                         for l, x, y in zip(legs, e, ex)]}


def _book(r: dict, risk_unit: float, budget: float, n_legs: int) -> dict:
    if "skip" in r:
        return r
    if risk_unit <= 0:
        return {"skip": "non-positive risk per unit"}
    n = math.floor(budget / risk_unit)
    if n < 1:
        return {"skip": f"one unit risks ${risk_unit:,.0f}, over the ${budget:,.0f} budget"}
    gross = (r["cash0"] + r["cash1"]) * 100 * n
    costs = COMMISSION * n * n_legs * 2
    return {**r, "contracts": n, "risk_per_unit": risk_unit, "gross": gross, "costs": costs, "net": gross - costs,
            "net_mid_fills": (r["mid1"] - r["mid0"]) * 100 * n - costs}


def _nearest(arr: np.ndarray, x: float) -> float | None:
    return float(arr[np.abs(arr - x).argmin()]) if len(arr) else None


def _expiry(Q: Quotes, day: dt.date, min_days: int) -> dt.date | None:
    c = [e for e in Q.expiries() if (e - day).days >= min_days]
    return c[0] if c else None


def long_option(Q, day, side, t0, t1, S0, min_days, budget, stop=0.5) -> dict:
    exp = _expiry(Q, day, min_days)
    if exp is None:
        return {"skip": "no expiry"}
    right = "C" if side > 0 else "P"
    K = _nearest(Q.strikes(exp, right), S0)
    r = _run(Q, [(exp, right, K, +1)], t0, t1, lambda v, s: v <= s["mid0"] * (1 - stop))
    if "skip" in r:
        return r
    return _book(r, -r["cash0"] * 100 * stop, budget, 1)


def credit_spread(Q, day, side, t0, t1, S0, width, budget, stop_mult=2.0) -> dict:
    exp = _expiry(Q, day, 0)
    if exp is None:
        return {"skip": "no expiry"}
    if side > 0:
        ks = Q.strikes(exp, "P")
        below = ks[ks <= S0]
        if not len(below):
            return {"skip": "no strike"}
        k1 = float(below.max()); legs = [(exp, "P", k1, -1), (exp, "P", k1 - width, +1)]
    else:
        ks = Q.strikes(exp, "C")
        above = ks[ks >= S0]
        if not len(above):
            return {"skip": "no strike"}
        k1 = float(above.min()); legs = [(exp, "C", k1, -1), (exp, "C", k1 + width, +1)]
    if legs[1][2] not in set(ks):
        return {"skip": "wing strike not listed"}
    r = _run(Q, legs, t0, t1, lambda v, s: -v >= stop_mult * s["cash0"])
    if "skip" in r:
        return r
    if r["cash0"] <= 0:
        return {"skip": "no credit at the natural"}
    return _book(r, (width - r["cash0"]) * 100, budget, 2)


def condor(Q, day, t0, t1, S0, dist, wing, budget, stop_mult=2.0) -> dict:
    exp = _expiry(Q, day, 0)
    if exp is None or exp != day:
        return {"skip": "no 0DTE expiry"}
    kc, kp = Q.strikes(exp, "C"), Q.strikes(exp, "P")
    sc, sp = kc[kc >= S0 * (1 + dist)], kp[kp <= S0 * (1 - dist)]
    if not len(sc) or not len(sp):
        return {"skip": "no strike"}
    sc, sp = float(sc.min()), float(sp.max())
    if sc + wing not in set(kc) or sp - wing not in set(kp):
        return {"skip": "wing strike not listed"}
    legs = [(exp, "C", sc, -1), (exp, "C", sc + wing, +1), (exp, "P", sp, -1), (exp, "P", sp - wing, +1)]
    r = _run(Q, legs, t0, t1, lambda v, s: -v >= stop_mult * s["cash0"])
    if "skip" in r:
        return r
    if r["cash0"] <= 0:
        return {"skip": "no credit at the natural"}
    return _book(r, (wing - r["cash0"]) * 100, budget, 4)


def _ts(day: dt.date, hhmm: str) -> pd.Timestamp:
    h, m = map(int, hhmm.split(":"))
    return pd.Timestamp(dt.datetime.combine(day, dt.time(h, m)), tz=NY).tz_convert("UTC")


CONDORS = {f"condor_{e.replace(':', '')}_{int(d * 1e4)}": (e, d) for e in ("09:45", "10:00") for d in (0.003, 0.006)}


def evaluate_day(day: dt.date, quotes: pd.DataFrame, bars: pd.DataFrame, share_trades: list[dict],
                 budget: float = 250.0, exit_hhmm: str = "10:55") -> list[dict]:
    """bars: SPY 1-minute bars (UTC index, labelled by start). share_trades: the day's shadow trades."""
    Q = Quotes(quotes)
    out = []
    open_at = lambda t: float(bars.loc[bars.index >= t, "open"].iloc[0]) if (bars.index >= t).any() else None
    for tr in share_trades:
        t0, t1 = pd.Timestamp(tr["entry_time"]).tz_convert("UTC"), pd.Timestamp(tr["exit_time"]).tz_convert("UTC")
        S0, side = float(tr["entry_raw"]), int(tr["side"])
        base = {"day": str(day), "strategy": tr["strategy"], "side": side, "entry_time": str(t0), "exit_time": str(t1),
                "S_entry": S0, "underlying_net": tr.get("net")}
        out.append({**base, "rule": "long_0dte", **long_option(Q, day, side, t0, t1, S0, 0, budget)})
        out.append({**base, "rule": "long_1dte", **long_option(Q, day, side, t0, t1, S0, 1, budget)})
        out.append({**base, "rule": "credit_2w", **credit_spread(Q, day, side, t0, t1, S0, 2.0, budget)})
    t_exit = _ts(day, exit_hhmm)
    for name, (e, d) in CONDORS.items():
        t0 = _ts(day, e)
        S0 = open_at(t0)
        r = condor(Q, day, t0, t_exit, S0, d, 3.0, budget) if S0 else {"skip": "no underlying bar"}
        out.append({"day": str(day), "strategy": "none", "rule": name, "entry_time": str(t0), "exit_time": str(t_exit),
                    "S_entry": S0, **r})
    return out


def calibration(day: dt.date, quotes: pd.DataFrame, bars: pd.DataFrame, vix_prev: float | None,
                at: str = "09:35", until: str = "10:55") -> dict:
    """Real 0DTE ATM straddle vs the MODELED one, the real ATM spread, quote delay, and the mid-price
    change of a short ATM straddle from `at` to `until` (a measurement only: undefined risk, never traded)."""
    Q = Quotes(quotes)
    t0, t1 = _ts(day, at), _ts(day, until)
    rec = {"day": str(day), "median_delay_s": float(np.nanmedian(quotes["delay_s"])) if "delay_s" in quotes else None,
           "snapshots": len(Q.times)}
    exp = _expiry(Q, day, 0)
    S0 = float(bars.loc[bars.index >= t0, "open"].iloc[0]) if (bars.index >= t0).any() else None
    if exp is None or S0 is None:
        return {**rec, "skip": "no expiry or bar"}
    K = _nearest(np.intersect1d(Q.strikes(exp, "C"), Q.strikes(exp, "P")), S0)
    c, p = Q.at(t0, exp, "C", K), Q.at(t0, exp, "P", K)
    if c is None or p is None:
        return {**rec, "skip": "no ATM quote"}
    st = (c[0] + c[1]) / 2 + (p[0] + p[1]) / 2
    rec.update({"expiry": str(exp), "strike": K, "S": S0, "straddle_mid": st, "straddle_pct": st / S0,
                "atm_call_half_spread_pct": (c[1] - c[0]) / 2 / ((c[0] + c[1]) / 2),
                "atm_put_half_spread_pct": (p[1] - p[0]) / 2 / ((p[0] + p[1]) / 2)})
    if vix_prev:
        from .overlay import OptionParams, _T, _iv
        from .pricing import bs_price
        prm = OptionParams()
        T = _T(c[2], exp)
        m = bs_price(S0, K, T, _iv(vix_prev, S0, K, prm), True) + bs_price(S0, K, T, _iv(vix_prev, S0, K, prm), False)
        rec.update({"vix_prev": vix_prev, "model_straddle": m, "real_over_model": st / m})
    c1, p1 = Q.at(t1, exp, "C", K), Q.at(t1, exp, "P", K)
    if c1 and p1:
        st1 = (c1[0] + c1[1]) / 2 + (p1[0] + p1[1]) / 2
        rec.update({"straddle_mid_end": st1, "short_straddle_mid_pnl": (st - st1) * 100})
    return rec
