import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from tradesys.options import listing
from tradesys.options.overlay import OptionParams, _T, overlay
from tradesys.options.pricing import bs_delta, bs_price, strike_for_delta, variance_time


def test_put_call_parity_and_delta():
    S, K, T, v = 500.0, 502.0, 0.01, 0.2
    c, p = bs_price(S, K, T, v, True), bs_price(S, K, T, v, False)
    assert math.isclose(c - p, S - K, abs_tol=1e-9)
    assert 0.3 < bs_delta(S, K, T, v, True) < 0.5 and -0.7 < bs_delta(S, K, T, v, False) < -0.5
    K30 = strike_for_delta(S, T, v, True, 0.3)
    assert K30 > S and abs(bs_delta(S, K30, T, v, True) - 0.3) < 0.06


def test_expiry_history():
    assert listing.nearest(dt.date(2015, 3, 4)) == dt.date(2015, 3, 6)       # no Wednesday expiries yet
    assert listing.nearest(dt.date(2017, 3, 1)) == dt.date(2017, 3, 1)       # Wednesday 0DTE
    assert listing.nearest(dt.date(2019, 7, 2)) == dt.date(2019, 7, 3)       # Tuesday: next is Wednesday
    assert listing.nearest(dt.date(2023, 3, 2)) == dt.date(2023, 3, 2)       # Thursday 0DTE (from Nov 2022)
    assert listing.nearest(dt.date(2024, 3, 28)) == dt.date(2024, 3, 28)     # Good Friday: Thursday expiry
    assert listing.nearest(dt.date(2023, 3, 2), min_dte=1) == dt.date(2023, 3, 3)


def test_variance_time_shrinks_through_the_day():
    a = _T(pd.Timestamp("2023-03-02 09:35", tz="America/New_York"), dt.date(2023, 3, 2))
    b = _T(pd.Timestamp("2023-03-02 10:55", tz="America/New_York"), dt.date(2023, 3, 2))
    c = _T(pd.Timestamp("2023-03-02 09:35", tz="America/New_York"), dt.date(2023, 3, 3))
    assert 0 < b < a < c
    assert math.isclose(a, variance_time(385, 0, 0))
    # 2 sessions ahead: today's 385 min + 1 full session + the expiry session + 2 nights = 0.711+0.72+0.72+0.56 days
    d = _T(pd.Timestamp("2025-03-05 09:35", tz="America/New_York"), dt.date(2025, 3, 7))
    assert math.isclose(d * 252, 0.72 * 385 / 390 + 0.72 + 0.72 + 2 * 0.28, rel_tol=1e-9)


def test_overlay_long_call_pays_spread_and_commission():
    day = dt.date(2023, 3, 2)
    idx = pd.date_range(pd.Timestamp("2023-03-02 09:30", tz="America/New_York"), periods=90, freq="1min")
    px = np.linspace(400, 402, 90)
    bars = pd.DataFrame({"open": px, "high": px + 0.05, "low": px - 0.05, "close": px}, index=idx)
    tr = pd.DataFrame([{"day": day, "side": 1, "entry_raw": px[6], "entry_time": idx[6], "exit_raw": px[85],
                        "exit_time": idx[85], "net": 0.0}])
    ctx = pd.DataFrame({"vix_prev": [20.0]}, index=pd.Index([day]))
    o = overlay(tr, {day: bars}, ctx, OptionParams(delta=0.5), "normal")
    r = o.iloc[0]
    assert r.right == "C" and r.dte == 0 and r.contracts >= 1
    assert r.exit_mid > r.entry_mid                                           # underlying rose 1.6
    assert r.net < r.frictionless and r.costs == pytest.approx(2 * 0.69 * r.contracts)
