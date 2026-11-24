"""In-sample research runner: pre-declared parameter grids, every trial logged, walk-forward
selection, cost scenarios, bootstrap p-values with a Holm correction across families.

Refuses any session on or after the out-of-sample start: the holdout is only reachable through
a frozen spec (main.py backtest --frozen).

Walk-forward: for each test year Y (from the third year of data), pick the grid point with the
best in-sample Sharpe over all years before Y under the UNFAVORABLE cost scenario (at least
`min_trades` trades, else no trade), then record that choice's daily P&L in year Y. The joined
test years are an honest estimate of what the selection process would have earned.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..analytics.metrics import daily_returns, p_value_mean_positive, sharpe_ci, summary
from ..config import ROOT, Config
from ..feature_engineering.features import daily_context, intraday
from ..strategies.library import make
from .engine import Backtester, prepare_sessions

GRIDS: dict[str, dict[str, list]] = {
    "orb": {"minutes": [5, 15, 30], "stop": ["mid", "far"], "target_r": [1.0, 2.0, None]},
    "gap_fill": {"min_gap": [0.001, 0.002], "max_gap": [0.005, 0.01], "entry_minute": [0, 4, 14]},
    "sweep": {"levels": [("prev",), ("pm",), ("prev", "pm")], "target_r": [1.0, 2.0]},
    "vwap_trend": {"start_minute": [15, 30], "stop_atr": [1.0, 2.0], "target_r": [1.5, 2.5]},
    "vwap_reversion": {"stretch_atr": [2.5, 3.5], "rsi_extreme": [20.0, 30.0], "stop_atr": [1.0, 2.0]},
    "momentum": {"start_minute": [15, 30], "min_relvol": [1.0, 1.5], "stop_atr": [1.5, 3.0], "target_r": [1.5, 3.0]},
    # declared 2026-11-24 after the in-sample time-of-day study, before any open_drive backtest
    "open_drive": {"minute": [4, 9], "k": [0.1, 0.2], "max_hold_min": [10, 20]},
    # declared 2026-11-24 after the in-sample finding that costs, not signals, sink these two
    # families; limit entries at the signal close, filled only on a one-tick trade-through
    "gap_fill_limit": {"min_gap": [0.001, 0.002], "max_gap": [0.005, 0.01], "entry_minute": [0, 4, 14],
                       "entry": ["limit"]},
    "vwap_reversion_limit": {"stretch_atr": [2.5, 3.5], "rsi_extreme": [20.0, 30.0], "stop_atr": [1.0, 2.0],
                             "entry": ["limit"]},
}
BASE = {"gap_fill_limit": "gap_fill", "vwap_reversion_limit": "vwap_reversion"}   # grid name -> strategy class
NEEDS_VOLUME = {"vwap_trend", "vwap_reversion", "momentum", "vwap_reversion_limit"}


def grid(name: str) -> list[dict[str, Any]]:
    g = GRIDS[name]
    return [dict(zip(g, vals)) for vals in itertools.product(*g.values())]


def _key(params: dict) -> str:
    return json.dumps(params, sort_keys=True, default=list)


class Study:
    def __init__(self, cfg: Config, bars: pd.DataFrame, source: str, dividends: pd.Series | None = None,
                 out_dir: Path | None = None):
        oos0 = cfg.validation.out_of_sample[0]
        cut = pd.Timestamp(dt.datetime.combine(oos0, dt.time(0)), tz="America/New_York")
        if (bars.index >= cut).any():
            bars = bars[bars.index < cut]               # the holdout never enters in-sample research
        self.cfg, self.source = cfg, source
        self.feats, self.ctx = intraday(bars), daily_context(bars, dividends)
        self.days = sorted(set(self.feats["day"]))
        self.prep = prepare_sessions(cfg, self.feats)                 # sliced once, reused by every run
        self.has_volume = bool((bars["volume"] > 0).mean() > 0.5)
        self.out = out_dir or ROOT / "reports" / "research"
        self.out.mkdir(parents=True, exist_ok=True)
        self.trial_log = self.out / "trial_log.jsonl"
        self.daily: dict[tuple[str, str, str], pd.Series] = {}      # (strategy, params, scenario) -> daily pnl
        self.trades: dict[tuple[str, str, str], pd.DataFrame] = {}

    def run(self, name: str, scenarios=("normal", "unfavorable", "extreme"), params_list=None) -> pd.DataFrame:
        if name in NEEDS_VOLUME and not self.has_volume:
            return pd.DataFrame()
        rows = []
        for params in params_list or grid(name):
            for sc in scenarios:
                t0 = time.time()
                res = Backtester(self.cfg, make(BASE.get(name, name), **params), sc).run(
                    None, self.ctx, self.days, prepared=self.prep)
                k = (name, _key(params), sc)
                self.daily[k], self.trades[k] = res.daily, res.trades
                s = summary(res.daily, res.trades, res.starting_equity)
                rec = {"source": self.source, "strategy": name, "params": params, "scenario": sc,
                       "first": str(self.days[0]), "last": str(self.days[-1]), **{
                           x: s.get(x) for x in ("trades", "net_profit", "sharpe", "max_drawdown", "win_rate",
                                                 "profit_factor", "expectancy", "expectancy_r", "total_costs")},
                       "secs": round(time.time() - t0, 1), "logged_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
                with self.trial_log.open("a") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
                rows.append(rec)
        return pd.DataFrame(rows)

    def per_year(self, name: str, scenario: str = "normal") -> pd.DataFrame:
        out = {}
        for (n, p, sc), d in self.daily.items():
            if n == name and sc == scenario:
                out[p] = d.groupby([x.year for x in d.index]).sum()
        return pd.DataFrame(out).T

    def walk_forward(self, name: str, select_scenario: str = "unfavorable", min_trades: int = 30,
                     first_test_offset: int = 2) -> dict[str, Any]:
        keys = sorted({p for (n, p, sc) in self.daily if n == name})
        if not keys:
            return {}
        years = sorted({d.year for d in self.days})
        test_years = years[first_test_offset:]
        eq0 = self.cfg.risk.starting_equity
        chosen, pieces = {}, {sc: [] for sc in ("normal", "unfavorable", "extreme")}
        for y in test_years:
            best, best_sh = None, -math.inf
            for p in keys:
                d = self.daily[(name, p, select_scenario)]
                tr = self.trades[(name, p, select_scenario)]
                train = d[[x.year < y for x in d.index]]
                ntr = int((pd.to_datetime(tr["day"]).dt.year < y).sum()) if len(tr) else 0
                if ntr < min_trades or len(train) < 100:
                    continue
                r = train / eq0
                sh = r.mean() / r.std(ddof=1) * math.sqrt(252) if r.std(ddof=1) > 0 else -math.inf
                if sh > best_sh:
                    best, best_sh = p, sh
            chosen[y] = {"params": best, "train_sharpe": None if best is None else round(best_sh, 3)}
            for sc in pieces:
                if best is None or best_sh <= 0:                        # nothing worth trading: stay in cash
                    d = self.daily[(name, keys[0], sc)]
                    pieces[sc].append(d[[x.year == y for x in d.index]] * 0.0)
                else:
                    d = self.daily[(name, best, sc)]
                    pieces[sc].append(d[[x.year == y for x in d.index]])
        out: dict[str, Any] = {"strategy": name, "source": self.source, "test_years": test_years, "chosen": chosen}
        for sc, ps in pieces.items():
            d = pd.concat(ps) if ps else pd.Series(dtype=float)
            r = daily_returns(d, eq0)
            n_tr = sum(int(((pd.to_datetime(self.trades[(name, c["params"], sc)]["day"]).dt.year == y)).sum())
                       for y, c in chosen.items() if c["params"] and c["train_sharpe"] and c["train_sharpe"] > 0
                       and len(self.trades[(name, c["params"], sc)]))
            lo, hi = sharpe_ci(r, n_boot=500) if len(r) > 40 else (float("nan"), float("nan"))
            out[sc] = {"net_profit": float(d.sum()), "sessions": len(d), "trades": n_tr,
                       "sharpe": float(r.mean() / r.std(ddof=1) * math.sqrt(252)) if r.std(ddof=1) > 0 else 0.0,
                       "sharpe_ci95": [lo, hi], "p_mean_le_0": p_value_mean_positive(r, n_boot=500) if len(r) > 40 else float("nan"),
                       "by_year": {int(k): float(v) for k, v in d.groupby([x.year for x in d.index]).sum().items()}}
        return out


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values across strategy families."""
    items = sorted(((p if math.isfinite(p) else 1.0), k) for k, p in pvals.items())
    m, out, run = len(items), {}, 0.0
    for i, (p, k) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out
