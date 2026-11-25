"""Options overlay (MODELED): express an underlying strategy's trades as long SPY calls / puts.

For each underlying trade (direction, entry bar, exit bar from the share backtest), buy a call
(long) or put (short) at the target |delta| on the nearest listed expiry with at least `min_dte`
days, price it with Black-Scholes (pricing.py) from the previous session's VIX (plus a mild skew),
pay the modeled ask, then walk the position minute by minute: an optional premium stop (checked
at the bar's worst underlying price) and premium target (checked at bar closes), else exit at the
underlying trade's exit, receiving the modeled bid. Sized by premium at risk:
contracts = floor(equity * risk_per_trade / (premium at risk per contract)), where premium at risk
is the full premium without a premium stop, or the stop fraction of it with one.

Not modeled (stated limits): implied-volatility changes during the trade (vega), the volatility
smile beyond a linear skew, early-exercise effects, quote gaps and the real bid/ask of each strike.
Results from this module are MODELED, never verified historical options results.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd

from ..market_calendar import NY, session_close, sessions
from . import listing
from .pricing import OVERNIGHT_SHARE, bs_price, strike_for_delta, variance_time

SCENARIOS = {  # half spread as a share of premium (min half a cent), commission + fees per contract per side
    "normal": {"spread_pct": 0.004, "min_half": 0.005, "commission": 0.69},
    "unfavorable": {"spread_pct": 0.008, "min_half": 0.01, "commission": 0.69},
    "extreme": {"spread_pct": 0.016, "min_half": 0.02, "commission": 1.00},
}


@dataclass
class OptionParams:
    delta: float = 0.5
    min_dte: int = 0
    premium_stop: float | None = None        # exit if the option loses this fraction of its entry price
    premium_target: float | None = None      # exit if it gains this fraction
    iv_mult: float = 1.15                    # VIX x 1.15: model 0DTE straddles sit near market levels (~0.7% of spot)
    skew: float = 1.5                        # IV * (1 - skew * ln(K/S)): puts below spot dearer


def _iv(vix: float, S: float, K: float, p: OptionParams) -> float:
    return float(np.clip(vix / 100 * p.iv_mult * (1 - p.skew * math.log(K / S)), 0.05, 1.5))


@lru_cache(maxsize=100_000)
def _T_parts(day: dt.date, expiry: dt.date) -> tuple[float, int, float]:
    """(today's close as minutes after midnight, sessions strictly between, expiry-day session minutes)."""
    c = session_close(day)
    close_min = c.hour * 60 + c.minute
    if expiry <= day:
        return close_min, -1, 0.0
    mid = [d for d in sessions(day + dt.timedelta(days=1), expiry) if d < expiry]
    ec = session_close(expiry)
    return close_min, len(mid), (ec.hour * 60 + ec.minute) - 570.0


def _T(t: pd.Timestamp, expiry: dt.date) -> float:
    """Variance time from t to the expiry's close: what is left of today's session, every full
    session in between, the expiry day's session, and one overnight per night crossed."""
    tn = t.tz_convert(NY)
    close_min, n_mid, exp_min = _T_parts(tn.date(), expiry)
    left = max(close_min - (tn.hour * 60 + tn.minute + tn.second / 60), 0.0)
    if n_mid < 0:
        return variance_time(left, 0, 0)
    return variance_time(left, n_mid, n_mid + 1) + variance_time(exp_min, 0, 0)


def _half(price: float, sc: dict) -> float:
    return max(sc["min_half"], sc["spread_pct"] * price)


def overlay(trades: pd.DataFrame, feats_by_day: dict, ctx: pd.DataFrame, p: OptionParams, scenario: str,
            equity: float = 100_000, risk_per_trade: float = 0.0025) -> pd.DataFrame:
    sc = SCENARIOS[scenario]
    rows, skipped = [], []
    for _, t in trades.iterrows():
        day = t["day"]
        vix = ctx.loc[day].get("vix_prev") if day in ctx.index else None
        if vix is None or not np.isfinite(vix):
            skipped.append((day, "no VIX for the day"))
            continue
        exp = listing.nearest(day, p.min_dte)
        if exp is None:
            skipped.append((day, "no listed expiry"))
            continue
        call = t["side"] > 0
        S0, t0 = float(t["entry_raw"]), pd.Timestamp(t["entry_time"])
        T0 = _T(t0, exp)
        iv0 = _iv(vix, S0, S0, p)
        K = strike_for_delta(S0, T0, iv0, call, p.delta)
        iv = _iv(vix, S0, K, p)
        mid0 = bs_price(S0, K, T0, iv, call)
        if mid0 < 0.05:
            skipped.append((day, "option under 5 cents"))
            continue
        ask = mid0 + _half(mid0, sc)
        at_risk = ask * 100 * (p.premium_stop if p.premium_stop else 1.0)
        n = math.floor(equity * risk_per_trade / at_risk)
        if n < 1:
            skipped.append((day, f"one contract risks ${at_risk:,.0f}, over the ${equity * risk_per_trade:,.0f} budget"))
            continue
        bars = feats_by_day[day]
        path = bars[(bars.index >= t0) & (bars.index < pd.Timestamp(t["exit_time"]))]
        exit_t, exit_mid, why = pd.Timestamp(t["exit_time"]), None, "underlying exit"
        per_min = (1 - OVERNIGHT_SHARE) / 390 / 252                # variance time burnt per session minute
        mins = ((path.index - t0).total_seconds().to_numpy() / 60) + 1
        for m_, lo_, hi_, cl_ in zip(mins, path["low"].to_numpy(), path["high"].to_numpy(), path["close"].to_numpy()):
            ts = t0 + pd.Timedelta(minutes=float(m_) - 1)
            Tb = max(T0 - m_ * per_min, 1e-9)
            worst = lo_ if call else hi_
            if p.premium_stop and bs_price(worst, K, Tb, iv, call) <= mid0 * (1 - p.premium_stop):
                exit_t, exit_mid, why = ts + pd.Timedelta(minutes=1), bs_price(worst, K, Tb, iv, call), "premium stop"
                break
            m = bs_price(cl_, K, Tb, iv, call)
            if p.premium_target and m >= mid0 * (1 + p.premium_target):
                exit_t, exit_mid, why = ts + pd.Timedelta(minutes=1), m, "premium target"
                break
        if exit_mid is None:
            exit_mid = bs_price(float(t["exit_raw"]), K, _T(exit_t, exp), iv, call)
        bid = max(0.0, exit_mid - _half(exit_mid, sc))
        gross = (bid - ask) * 100 * n
        costs = 2 * sc["commission"] * n
        net = gross - costs
        rows.append({"day": day, "side": int(t["side"]), "right": "C" if call else "P", "strike": K, "expiry": exp,
                     "dte": (exp - day).days, "contracts": n, "iv": iv, "entry_time": t0, "exit_time": exit_t,
                     "S_entry": S0, "entry_mid": mid0, "entry_ask": ask, "exit_mid": exit_mid, "exit_bid": bid,
                     "exit_reason": why, "gross": gross, "costs": costs, "net": net,
                     "frictionless": (exit_mid - mid0) * 100 * n, "premium_at_risk": ask * 100 * n,
                     "r_multiple": net / (at_risk * n), "hold_min": (exit_t - t0).total_seconds() / 60,
                     "underlying_net": float(t["net"])})
    out = pd.DataFrame(rows)
    out.attrs["skipped"] = skipped
    return out
