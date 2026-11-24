import math

import numpy as np
import pandas as pd

from tradesys.analytics.metrics import daily_returns, sharpe_ci, summary


def test_summary_on_known_series():
    pnl = pd.Series([100.0, -50.0, 0.0, 200.0, -100.0])
    tr = pd.DataFrame({"net": [100.0, -50.0, 200.0, -100.0], "r_multiple": [1, -0.5, 2, -1], "hold_min": [5, 5, 5, 5],
                       "costs": [1, 1, 1, 1], "frictionless": [102, -48, 202, -98], "gross": [101, -49, 201, -99]})
    s = summary(pnl, tr, 10_000)
    r = daily_returns(pnl, 10_000)
    assert math.isclose(s["sharpe"], r.mean() / r.std(ddof=1) * math.sqrt(252))
    assert math.isclose(s["max_drawdown"], 100 / 10_250)          # 10,250 -> 10,150
    assert s["win_rate"] == 0.5 and s["max_consec_losses"] == 1 and math.isclose(s["profit_factor"], 2.0)
    assert math.isclose(s["total_costs"], 8.0)


def test_bootstrap_ci_brackets_a_strong_edge():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.001, 0.005, 1000))
    lo, hi = sharpe_ci(r, n_boot=300)
    assert lo > 0 and lo < hi
