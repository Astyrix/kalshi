"""Shared test helpers. SYNTHETIC DATA ONLY: these bars test mechanics (fills, timing,
leakage), never strategy performance."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tradesys.config import load_config
from tradesys.market_calendar import NY, session_close, sessions


def synthetic_bars(start: dt.date, end: dt.date, seed: int = 1, s0: float = 400.0,
                   premarket: bool = True) -> pd.DataFrame:
    """Random-walk 1-minute bars (UTC index) for every NYSE session, 4:00 to the close."""
    rng = np.random.default_rng(seed)
    out, px = [], s0
    for d in sessions(start, end):
        t0 = dt.time(4, 0) if premarket else dt.time(9, 30)
        a = pd.Timestamp(dt.datetime.combine(d, t0), tz=NY)
        b = pd.Timestamp(dt.datetime.combine(d, session_close(d)), tz=NY)
        idx = pd.date_range(a, b, freq="1min", inclusive="left")
        r = rng.normal(0, 0.0006, len(idx))
        c = px * np.exp(np.cumsum(r))
        o = np.r_[px, c[:-1]]
        h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0002, len(idx))))
        l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0002, len(idx))))
        v = rng.integers(2000, 20000, len(idx))
        out.append(pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=idx))
        px = c[-1] * np.exp(rng.normal(0, 0.003))        # overnight gap
    df = pd.concat(out)
    df.index = df.index.tz_convert("UTC").rename("ts")
    return df


@pytest.fixture
def cfg():
    return load_config()
