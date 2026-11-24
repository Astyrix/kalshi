"""Model-free structure of the first 90 minutes (in-sample, Oanda 2010-2019, SPY-scaled).

For 15-minute buckets 9:30-11:00: mean absolute return (opportunity), mean return (drift),
lag-1 autocorrelation of 5-minute returns (momentum > 0, reversal < 0). And predictive
regressions across days: gap -> 9:30-10:00 and 10:00-11:00 returns; 9:30-10:00 -> 10:00-11:00.
t-statistics use Newey-West (5 lags) standard errors."""
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tradesys.data_ingestion.free_sources import spy_equivalent  # noqa: E402
from tradesys.feature_engineering.features import daily_context, regular_session  # noqa: E402


def nw_t(y, x, lags=5):
    X = np.c_[np.ones(len(x)), x]
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    e = y - X @ b
    S = (X * e[:, None]).T @ (X * e[:, None])
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        G = (X[l:] * e[l:, None]).T @ (X[:-l] * e[:-l, None])
        S += w * (G + G.T)
    XtXi = np.linalg.inv(X.T @ X)
    V = XtXi @ S @ XtXi
    return b[1], b[1] / np.sqrt(V[1, 1]), 1 - ((e ** 2).sum() / ((y - y.mean()) ** 2).sum())


yd = ROOT / "data" / "raw" / "yahoo"
spy, spx = pd.read_parquet(yd / "SPY_1d.parquet"), pd.read_parquet(yd / "GSPC_1d.parquet")
spy.index, spx.index = [d.date() for d in spy.index], [d.date() for d in spx.index]
raw = pd.read_parquet(ROOT / "data" / "raw" / "oanda" / "SPX500_USD_1m.parquet")
raw = raw[(raw.index >= "2009-12-01") & (raw.index < "2020-01-01")]
bars = spy_equivalent(raw, spy, spx)
r = regular_session(bars)
ctx = daily_context(bars)
r["minute"] = (r.index.hour - 9) * 60 + r.index.minute - 30
r = r[(r["minute"] < 90) & (r["day"] >= dt.date(2010, 1, 4))]
lines = ["# The first 90 minutes: model-free structure (in-sample)", "",
         f"Oanda SPX500 CFD scaled to SPY, {r['day'].min()} .. {r['day'].max()}, {r['day'].nunique()} sessions.", "",
         "| bucket | mean abs 15-min return (bp) | mean return (bp) | t | lag-1 autocorr of 5-min returns |", "|---|---|---|---|---|"]
for a in range(0, 90, 15):
    g = r[(r["minute"] >= a) & (r["minute"] < a + 15)]
    o = g.groupby("day")["open"].first()
    c = g.groupby("day")["close"].last()
    ret = (c / o - 1) * 1e4
    five = g[g["minute"] % 5 == 4].groupby("day")["close"].apply(lambda s: np.log(s).diff().dropna().to_numpy())
    pairs = np.concatenate([np.c_[x[:-1], x[1:]] for x in five if len(x) > 1]) if len(five) else np.empty((0, 2))
    ac = np.corrcoef(pairs[:, 0], pairs[:, 1])[0, 1] if len(pairs) > 10 else float("nan")
    t = ret.mean() / (ret.std(ddof=1) / np.sqrt(len(ret)))
    lines.append(f"| {9 + (30 + a) // 60}:{(30 + a) % 60:02d}-{9 + (45 + a) // 60}:{(45 + a) % 60:02d} | "
                 f"{ret.abs().mean():.1f} | {ret.mean():+.2f} | {t:+.2f} | {ac:+.3f} |")
d = r.groupby("day")
px = lambda m0: d.apply(lambda g: g.loc[g["minute"] >= m0, "open"].iloc[0] if (g["minute"] >= m0).any() else np.nan)  # noqa: E731
o0, o30 = px(0), px(30)
c90 = d["close"].last()
df = pd.DataFrame({"gap": ctx["gap_adj"].reindex(o0.index), "r1": (o30 / o0 - 1), "r2": (c90 / o30 - 1)}).dropna() * 1e4
lines += ["", "## Predictive regressions across days (bp; Newey-West t)", "", "| y | x | slope | t | R^2 |", "|---|---|---|---|---|"]
for y, x in (("r1", "gap"), ("r2", "gap"), ("r2", "r1")):
    b, t, r2 = nw_t(df[y].to_numpy(), df[x].to_numpy())
    lines.append(f"| {y} | {x} | {b:+.4f} | {t:+.2f} | {r2:.4f} |")
lines += ["", "r1 = 9:30-10:00 return, r2 = 10:00-11:00 return, gap = dividend-adjusted overnight gap.",
          "A slope with |t| < 2 is indistinguishable from zero; even |t| > 2 must survive costs and the holdout."]
out = ROOT / "reports" / "research" / "time_of_day.md"
out.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
