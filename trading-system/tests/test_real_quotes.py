"""Real-quote options scoring: fills come from the first quote at or after the decision, spreads
cross the market, stops fire on the mid, and sizing respects the budget."""
import datetime as dt

import pandas as pd
import pytest

from tradesys.options.real_quotes import Quotes, condor, credit_spread, long_option

DAY = dt.date(2026, 11, 27)
T = lambda hm: pd.Timestamp(f"2026-11-27 {hm}", tz="America/New_York").tz_convert("UTC")


def frame(snaps):
    """snaps: {quote time "HH:MM:SS": {(right, strike): (bid, ask)}}"""
    rows = []
    for hm, qs in snaps.items():
        for (r, k), (b, a) in qs.items():
            rows.append({"fetch_ts": T(hm) + pd.Timedelta(minutes=15), "quote_ts": T(hm), "expiry": DAY, "right": r,
                         "strike": float(k), "bid": b, "ask": a})
    return pd.DataFrame(rows)


def test_entry_uses_first_quote_at_or_after_decision():
    q = frame({"09:59:30": {("C", 700): (1.00, 1.02)}, "10:00:10": {("C", 700): (1.50, 1.52)},
               "10:30:05": {("C", 700): (2.00, 2.02)}})
    r = long_option(Quotes(q), DAY, +1, T("10:00:00"), T("10:30:00"), 700.0, 0, budget=250)
    assert r["legs"][0]["entry_ask"] == 1.52          # not the older, cheaper 09:59:30 quote
    assert r["legs"][0]["exit_bid"] == 2.00
    assert r["contracts"] == 3                        # 250 / (1.52 * 100 * 0.5) = 3.3
    assert r["gross"] == pytest.approx((2.00 - 1.52) * 100 * 3)
    assert r["net"] == pytest.approx(r["gross"] - 0.69 * 3 * 2)


def test_stale_quote_counts_as_missing():
    q = frame({"10:10:00": {("C", 700): (1.0, 1.02)}})
    assert "skip" in long_option(Quotes(q), DAY, +1, T("10:00:00"), T("10:30:00"), 700.0, 0, budget=250)


def test_premium_stop_on_mid():
    q = frame({"10:00:05": {("P", 700): (2.00, 2.04)}, "10:05:05": {("P", 700): (0.95, 0.99)},
               "10:30:05": {("P", 700): (3.0, 3.1)}})
    r = long_option(Quotes(q), DAY, -1, T("10:00:00"), T("10:30:00"), 700.0, 0, budget=250)
    assert r["exit_reason"] == "stop" and r["legs"][0]["exit_bid"] == 0.95


def test_bull_put_credit_spread_natural_fills():
    s0 = {("P", 700): (1.00, 1.04), ("P", 698): (0.40, 0.42)}
    s1 = {("P", 700): (0.50, 0.52), ("P", 698): (0.10, 0.12)}
    q = frame({"10:00:05": s0, "10:40:05": s1})
    r = credit_spread(Quotes(q), DAY, +1, T("10:00:00"), T("10:40:00"), 700.4, 2.0, budget=250)
    assert r["cash0"] == pytest.approx(1.00 - 0.42)   # sell 700 at the bid, buy 698 at the ask
    assert r["cash1"] == pytest.approx(-0.52 + 0.10)
    assert r["contracts"] == 1                         # max loss (2 - 0.58) * 100 = 142
    assert r["net"] == pytest.approx((0.58 - 0.42) * 100 - 0.69 * 2 * 2)


def test_condor_stop_at_twice_credit():
    s0 = {("C", 703): (0.50, 0.52), ("C", 706): (0.10, 0.11), ("P", 697): (0.50, 0.52), ("P", 694): (0.10, 0.11)}
    s1 = {("C", 703): (2.20, 2.24), ("C", 706): (0.50, 0.52), ("P", 697): (0.02, 0.03), ("P", 694): (0.0, 0.01)}
    q = frame({"09:45:05": s0, "10:10:05": s1, "10:55:05": s0})
    r = condor(Quotes(q), DAY, T("09:45:00"), T("10:55:00"), 700.0, 0.003, 3.0, budget=250)
    assert r["cash0"] == pytest.approx(0.50 - 0.11 + 0.50 - 0.11)
    assert r["exit_reason"] == "stop"
