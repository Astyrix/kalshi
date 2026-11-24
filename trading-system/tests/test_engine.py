"""Execution-rule tests on hand-built bars (synthetic, mechanics only)."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tradesys.backtesting.engine import Backtester
from tradesys.market_calendar import NY
from tradesys.strategies.base import Signal

DAY = dt.date(2024, 6, 12)


def bars(n=90, px=500.0):
    idx = pd.date_range(pd.Timestamp(dt.datetime.combine(DAY, dt.time(9, 30)), tz=NY), periods=n, freq="1min")
    f = pd.DataFrame({"open": px, "high": px + 0.05, "low": px - 0.05, "close": px, "volume": 1e6}, index=idx)
    f["day"], f["minute"], f["atr14"] = DAY, np.arange(n), 0.1
    return f


def ctx():
    return pd.DataFrame({"prev_close": [500.0]}, index=pd.Index([DAY], name="day"))


class OneShot:
    """Signals once at the close of bar `at` (minute index), nothing else."""
    name = "oneshot"
    params = {}

    def __init__(self, at, sig, exit_at=None):
        self.at, self.sig, self.exit_at = at, sig, exit_at

    def on_session_start(self, day, c):
        pass

    def on_bar_close(self, row, pos):
        if pos is not None and self.exit_at is not None and row["minute"] == self.exit_at:
            return "exit"
        return self.sig if row["minute"] == self.at and pos is None else None


def run(cfg, f, strat, scenario="normal"):
    return Backtester(cfg, strat, scenario).run(f, ctx())


def test_entry_fills_at_next_bar_open_plus_costs(cfg):
    f = bars()
    f.loc[f.index[16], "open"] = 501.0
    f.loc[f.index[16], ["high", "low", "close"]] = [501.05, 500.95, 501.0]
    r = run(cfg, f, OneShot(15, Signal(1, 499.0, None)))
    t = r.trades.iloc[0]
    sc = cfg.costs.scenarios["normal"]
    assert t.entry_time == f.index[16] and t.entry_raw == 501.0
    assert t.entry == pytest.approx(501.0 + sc.half_spread + sc.slippage)
    assert t.exit_reason == "flatten" and t.exit_time.time() == dt.time(10, 55)


def test_gap_through_stop_fills_at_the_open(cfg):
    f = bars()
    f.loc[f.index[30:], ["open", "high", "low", "close"]] = [498.0, 498.05, 497.95, 498.0]
    r = run(cfg, f, OneShot(15, Signal(1, 499.0, None)))
    t = r.trades.iloc[0]
    assert t.exit_reason == "stop (gap)" and t.exit_raw == 498.0
    assert t.exit == pytest.approx(498.0 - cfg.costs.scenarios["normal"].half_spread - cfg.costs.scenarios["normal"].slippage)


def test_both_touched_in_one_bar_counts_as_stop(cfg):
    f = bars()
    f.loc[f.index[20], ["high", "low"]] = [502.0, 498.0]
    r = run(cfg, f, OneShot(15, Signal(1, 499.0, 501.0)))
    assert r.trades.iloc[0].exit_reason == "stop"


def test_target_needs_a_trade_through(cfg):
    f = bars()
    f.loc[f.index[20], "high"] = 501.0                       # touches exactly: no fill
    r = run(cfg, f, OneShot(15, Signal(1, 499.0, 501.0)))
    assert r.trades.iloc[0].exit_reason == "flatten"
    f.loc[f.index[25], "high"] = 501.01                      # one tick through: fills at the limit
    r = run(cfg, f, OneShot(15, Signal(1, 499.0, 501.0)))
    t = r.trades.iloc[0]
    assert t.exit_reason == "target" and t.exit == 501.0


def test_no_entry_after_cutoff_and_flat_by_11(cfg):
    f = bars()
    r = run(cfg, f, OneShot(80, Signal(1, 499.0, None)))     # signal at 10:51
    assert r.trades.empty and r.signals.iloc[0].status.startswith("rejected: after entry cutoff")
    r = run(cfg, f, OneShot(79, Signal(1, 499.0, None)))     # signal at the 10:49 bar close -> fills 10:50
    assert len(r.trades) == 1 and r.trades.iloc[0].exit_time.time() <= dt.time(10, 55)


def test_open_beyond_stop_cancels_entry(cfg):
    f = bars()
    f.loc[f.index[16], ["open", "high", "low", "close"]] = [498.5, 498.6, 498.4, 498.5]
    r = run(cfg, f, OneShot(15, Signal(1, 499.0, None)))
    assert r.trades.empty and r.signals.iloc[0].status == "cancelled: opened beyond the stop"


def test_size_from_risk_and_volume_cap(cfg):
    f = bars()
    r = run(cfg, f, OneShot(15, Signal(1, 499.0, None)))
    # 100k * 0.25% = $250 risk; per-share risk ~1.0 + costs -> about 240 shares, under the leverage cap
    assert 200 <= r.trades.iloc[0].shares <= 250
    f["volume"] = 1000.0                                      # 10% cap -> 100 shares
    r = run(cfg, f, OneShot(15, Signal(1, 499.0, None)))
    assert r.trades.iloc[0].shares == 100 and r.trades.iloc[0].evidence.get("partial_fill")


def test_short_costs_and_fees(cfg):
    f = bars()
    f.loc[f.index[40:], ["open", "high", "low", "close"]] = [499.0, 499.05, 498.95, 499.0]
    r = run(cfg, f, OneShot(15, Signal(-1, 501.0, None), exit_at=45))
    t = r.trades.iloc[0]
    assert t.exit_reason == "strategy exit" and t.side == -1
    assert t.net < t.frictionless and t.costs > 0


def test_daily_loss_limit_halts_entries(cfg):
    from tradesys.risk_management.risk import RiskManager
    rm = RiskManager(cfg.risk)
    rm.start_session(DAY)
    rm.on_entry(); rm.on_exit(-1001.0)
    ok, why = rm.can_enter()
    assert not ok and "daily loss" in why
