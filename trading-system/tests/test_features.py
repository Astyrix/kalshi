import datetime as dt

import numpy as np
import pandas as pd

from conftest import synthetic_bars
from tradesys.feature_engineering.features import daily_context, intraday


def _same(a: pd.Series, b: pd.Series) -> bool:
    return bool(((a == b) | (a.isna() & b.isna()) | np.isclose(a, b, rtol=1e-12, atol=1e-12)).all())


def test_intraday_features_do_not_look_ahead():
    bars = synthetic_bars(dt.date(2024, 1, 2), dt.date(2024, 2, 20))
    full = intraday(bars)
    rng = np.random.default_rng(3)
    for t in rng.choice(full.index[full["minute"] < 90], 12, replace=False):
        cut = intraday(bars[bars.index <= t])
        a, b = full.loc[t], cut.loc[t]
        for col in full.columns:
            if col == "day":
                continue
            assert _same(pd.Series([a[col]]), pd.Series([b[col]])), f"{col} at {t} changes when later bars exist"


def test_daily_context_known_before_open():
    bars = synthetic_bars(dt.date(2024, 1, 2), dt.date(2024, 2, 20))
    full = daily_context(bars)
    day = full.index[-5]
    cut_at = pd.Timestamp(dt.datetime.combine(day, dt.time(9, 31)), tz="America/New_York")
    cut = daily_context(bars[bars.index < cut_at])
    for col in ["prev_close", "prev_high", "prev_low", "pm_high", "pm_low", "gap", "gap_adj", "atr_d14", "rv_d20",
                "ret_d1", "ret_d5", "sma_d20_dist", "prev_range", "low20", "high20"]:
        assert _same(pd.Series([full.loc[day, col]]), pd.Series([cut.loc[day, col]])), col


def test_opening_range_is_unknown_until_complete():
    f = intraday(synthetic_bars(dt.date(2024, 1, 2), dt.date(2024, 1, 10)))
    d = f[f["day"] == f["day"].iloc[-1]]
    assert d.loc[d["minute"] < 14, "or15_high"].isna().all()
    assert np.isclose(d.loc[d["minute"] == 14, "or15_high"].iloc[0], d.loc[d["minute"] < 15, "high"].max())


def test_boolean_context_flags_known_before_open():
    bars = synthetic_bars(dt.date(2024, 1, 2), dt.date(2024, 3, 20))
    full = daily_context(bars)
    day = full.index[-8]
    cut_at = pd.Timestamp(dt.datetime.combine(day, dt.time(9, 31)), tz="America/New_York")
    cut = daily_context(bars[bars.index < cut_at])
    for col in ["nr4", "nr7", "pre_holiday", "tom"]:
        assert bool(full.loc[day, col]) == bool(cut.loc[day, col]), col
    assert bool(full.loc[pd.Timestamp("2024-02-01").date(), "tom"]) and not bool(full.loc[pd.Timestamp("2024-02-14").date(), "tom"])
    assert bool(full.loc[pd.Timestamp("2024-02-16").date(), "pre_holiday"])          # Presidents' Day follows
