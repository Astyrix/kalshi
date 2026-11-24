"""End-to-end mechanics on SYNTHETIC bars: features -> every strategy -> backtest -> metrics.
These numbers mean nothing about real markets; the test only checks the pipeline holds its rules."""
import datetime as dt

import pandas as pd

from conftest import synthetic_bars
from tradesys.analytics.metrics import summary
from tradesys.backtesting.engine import Backtester
from tradesys.feature_engineering.features import daily_context, intraday
from tradesys.signal_engine.store import SignalStore
from tradesys.strategies.library import REGISTRY, make


def test_every_strategy_runs_and_obeys_the_window(cfg):
    bars = synthetic_bars(dt.date(2024, 1, 2), dt.date(2024, 3, 29), seed=5)
    feats, ctx = intraday(bars), daily_context(bars)
    days = sorted(set(feats["day"]))[25:]
    for name in REGISTRY:
        res = Backtester(cfg, make(name), "normal").run(feats, ctx, days)
        s = summary(res.daily, res.trades, res.starting_equity)
        assert s["sessions"] == len(days)
        if len(res.trades):
            et = res.trades["entry_time"].map(lambda t: t.time())
            xt = res.trades["exit_time"].map(lambda t: t.time())
            assert (et >= dt.time(9, 31)).all() and (et <= dt.time(10, 50)).all(), name
            assert (xt <= dt.time(10, 56)).all(), name
            assert (res.trades["entry_time"] > res.trades["signal_time"] - pd.Timedelta(seconds=1)).all()
            assert res.trades.groupby("day").size().max() <= cfg.risk.max_trades_per_session
            assert (res.trades["net"] <= res.trades["frictionless"] + 1e-9).all()     # costs never help
        assert not res.emergencies


def test_signal_store_refuses_uncalibrated_probability(tmp_path):
    st = SignalStore(tmp_path / "s.db")
    i = st.add(ts="2024-01-02T09:45:00-05:00", mode="backtest", instrument="SPY", strategy="orb", direction=1,
               status="filled", evidence={"or_high": 1.0})
    assert st.all()[0]["id"] == i
    import pytest
    with pytest.raises(ValueError):
        st.add(ts="x", mode="paper", instrument="SPY", strategy="orb", direction=1, status="new", probability=0.7)
