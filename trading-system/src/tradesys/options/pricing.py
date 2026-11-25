"""Black-Scholes pricing for short-dated SPY options (MODELED: no historical quotes available).

Time is measured in trading variance, not calendar time: the VIX is an annualised close-to-close
volatility, and about 28% of SPX daily variance happens overnight on average, so each remaining
regular-session minute carries 0.72/390 of a day's variance and each overnight 0.28.
"""
from __future__ import annotations

import math

from scipy.stats import norm

_SQ2 = math.sqrt(2.0)


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQ2))


OVERNIGHT_SHARE = 0.28


def variance_time(minutes_left_today: float, full_sessions_between: int, nights: int, session_minutes: float = 390) -> float:
    """Remaining variance time in years (trading days / 252)."""
    days = (1 - OVERNIGHT_SHARE) * max(minutes_left_today, 0) / session_minutes
    days += full_sessions_between * (1 - OVERNIGHT_SHARE) + nights * OVERNIGHT_SHARE
    return days / 252


def bs_price(S: float, K: float, T: float, sigma: float, call: bool, r: float = 0.0, q: float = 0.0) -> float:
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if call else (K - S))
    v = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / v
    d2 = d1 - v
    if call:
        return S * math.exp(-q * T) * _cdf(d1) - K * math.exp(-r * T) * _cdf(d2)
    return K * math.exp(-r * T) * _cdf(-d2) - S * math.exp(-q * T) * _cdf(-d1)


def bs_delta(S: float, K: float, T: float, sigma: float, call: bool) -> float:
    if T <= 0 or sigma <= 0:
        return (1.0 if S > K else 0.0) if call else (-1.0 if S < K else 0.0)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    return norm.cdf(d1) if call else norm.cdf(d1) - 1


def strike_for_delta(S: float, T: float, sigma: float, call: bool, delta: float, step: float = 1.0) -> float:
    """Listed strike (multiples of `step`) whose |delta| is closest to `delta`."""
    z = norm.ppf(delta) if call else -norm.ppf(delta)
    k = S * math.exp(-z * sigma * math.sqrt(max(T, 1e-9)) + 0.5 * sigma * sigma * T)
    lo = math.floor(k / step) * step
    cands = [lo - step, lo, lo + step, lo + 2 * step]
    return min(cands, key=lambda K: abs(abs(bs_delta(S, K, T, sigma, call)) - delta))
