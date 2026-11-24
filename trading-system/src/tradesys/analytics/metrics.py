"""Performance statistics from a backtest Result.

Annualised figures use the DAILY return series over every session in the test (days with no
trade count as 0 %), with 252 sessions a year: Sharpe = mean/std * sqrt(252), Sortino uses
the downside deviation of daily returns, Calmar = annualised return / max drawdown. Daily
returns are P&L over the day-start equity. Confidence intervals come from a stationary block
bootstrap over days (blocks of ~10 sessions) so streaks and volatility clustering survive.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def _streaks(x: np.ndarray) -> tuple[int, int]:
    best_w = best_l = cur_w = cur_l = 0
    for v in x:
        cur_w, cur_l = (cur_w + 1, 0) if v > 0 else (0, cur_l + 1) if v < 0 else (0, 0)
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    return best_w, best_l


def daily_returns(daily_pnl: pd.Series, start_equity: float) -> pd.Series:
    eq_start = start_equity + daily_pnl.cumsum().shift(1, fill_value=0.0)
    return daily_pnl / eq_start


def drawdowns(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    return equity / peak - 1


def summary(daily_pnl: pd.Series, trades: pd.DataFrame, start_equity: float) -> dict[str, Any]:
    r = daily_returns(daily_pnl, start_equity)
    eq = start_equity + daily_pnl.cumsum()
    n = len(r)
    years = n / 252 if n else float("nan")
    end = float(eq.iloc[-1]) if n else start_equity
    total = end / start_equity - 1
    ann = (1 + total) ** (1 / years) - 1 if n and years > 0 and end > 0 else float("nan")
    sd = r.std(ddof=1) if n > 1 else float("nan")
    down = r[r < 0]
    dd_sd = math.sqrt((down ** 2).sum() / n) if n else float("nan")
    dd = drawdowns(pd.concat([pd.Series([start_equity]), eq.reset_index(drop=True)]))
    mdd = float(-dd.min()) if len(dd) else 0.0
    out: dict[str, Any] = {
        "sessions": n, "starting_capital": start_equity, "ending_capital": end, "net_profit": end - start_equity,
        "total_return": total, "annual_return": ann, "annual_vol": sd * math.sqrt(252) if n > 1 else float("nan"),
        "sharpe": r.mean() / sd * math.sqrt(252) if n > 1 and sd > 0 else float("nan"),
        "sortino": r.mean() / dd_sd * math.sqrt(252) if n and dd_sd > 0 else float("nan"),
        "max_drawdown": mdd, "avg_drawdown": float(-dd[dd < 0].mean()) if (dd < 0).any() else 0.0,
        "calmar": ann / mdd if mdd > 0 and math.isfinite(ann) else float("nan"),
        "recovery_factor": (end - start_equity) / (mdd * start_equity) if mdd > 0 else float("nan"),
        "trades": int(len(trades)),
    }
    if len(trades):
        net = trades["net"].to_numpy(float)
        wins, losses = net[net > 0], net[net < 0]
        w, l = _streaks(net)
        out.update({
            "win_rate": float((net > 0).mean()), "avg_win": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss": float(losses.mean()) if len(losses) else 0.0,
            "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else float("inf"),
            "expectancy": float(net.mean()), "expectancy_r": float(trades["r_multiple"].mean()),
            "max_consec_wins": w, "max_consec_losses": l, "avg_hold_min": float(trades["hold_min"].mean()),
            "total_costs": float((trades["frictionless"] - trades["net"]).sum()),   # spread + slippage + fees
            "fees_and_commissions": float(trades["costs"].sum()),
            "frictionless_profit": float(trades["frictionless"].sum()),
            "trades_per_session": len(trades) / n if n else float("nan"),
        })
    return out


def block_bootstrap(daily_ret: pd.Series, stat, n_boot: int = 2000, block: int = 10, seed: int = 7) -> np.ndarray:
    """Stationary bootstrap (Politis-Romano) of a statistic of the daily return series."""
    x = daily_ret.to_numpy(float)
    n = len(x)
    if n < 2 * block:
        return np.array([])
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot)
    p = 1 / block
    for b in range(n_boot):
        idx = np.empty(n, dtype=int)
        i = rng.integers(n)
        for k in range(n):
            idx[k] = i
            i = rng.integers(n) if rng.random() < p else (i + 1) % n
        out[b] = stat(x[idx])
    return out


def sharpe_ci(daily_ret: pd.Series, level: float = 0.95, **kw) -> tuple[float, float]:
    f = lambda x: x.mean() / x.std(ddof=1) * math.sqrt(252) if x.std(ddof=1) > 0 else 0.0  # noqa: E731
    bs = block_bootstrap(daily_ret, f, **kw)
    if not len(bs):
        return float("nan"), float("nan")
    a = (1 - level) / 2
    return float(np.quantile(bs, a)), float(np.quantile(bs, 1 - a))


def p_value_mean_positive(daily_ret: pd.Series, **kw) -> float:
    """Bootstrap p-value for H0: mean daily return <= 0 (centred bootstrap)."""
    x = daily_ret - daily_ret.mean()
    bs = block_bootstrap(x, lambda v: v.mean(), **kw)
    if not len(bs):
        return float("nan")
    return float((bs >= daily_ret.mean()).mean())
