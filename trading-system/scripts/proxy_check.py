"""Proxy vs real SPY on the same days: Dukascopy S&P 500 (scaled to SPY dollars) against
Yahoo's real SPY 1-minute bars, 9:30-11:00 New York. Writes reports/proxy_vs_spy.md."""
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tradesys.data_ingestion.free_sources import duka_bars, spy_equivalent  # noqa: E402
from tradesys.feature_engineering.features import intraday  # noqa: E402

yd = ROOT / "data" / "raw" / "yahoo"
spy_d, spx_d = pd.read_parquet(yd / "SPY_1d.parquet"), pd.read_parquet(yd / "GSPC_1d.parquet")
spy_d.index, spx_d.index = [d.date() for d in spy_d.index], [d.date() for d in spx_d.index]
real = pd.read_parquet(yd / "SPY_1m_recent.parquet")
real = real[real.index.second == 0]
days = sorted({t.tz_convert("America/New_York").date() for t in real.index})
prox = spy_equivalent(duka_bars(ROOT / "data", days[0], days[-1]), spy_d, spx_d)
fr, fp = intraday(real), intraday(prox)
w = lambda f: f[f["minute"] < 90]  # noqa: E731
a, b = w(fr), w(fp)
j = a[["open", "high", "low", "close", "or15_high", "or15_low", "day", "minute"]].join(
    b[["open", "high", "low", "close", "or15_high", "or15_low"]], rsuffix="_p", how="inner")
r_real, r_prox = np.log(j["close"]).groupby(j["day"]).diff(), np.log(j["close_p"]).groupby(j["day"]).diff()
ok = r_real.notna() & r_prox.notna()
corr1 = np.corrcoef(r_real[ok], r_prox[ok])[0, 1]
r5 = lambda c: np.log(c).groupby(j["day"]).diff(5)  # noqa: E731
o5 = r5(j["close"]).notna() & r5(j["close_p"]).notna()
corr5 = np.corrcoef(r5(j["close"])[o5], r5(j["close_p"])[o5])[0, 1]
lvl = (j["close_p"] / j["close"] - 1) * 1e4
rng = ((j["high_p"] - j["low_p"]) / (j["high"] - j["low"]).replace(0, np.nan)).median()
# ORB-15 direction agreement: first close beyond the range after 9:45, per day
def first_break(f, sfx=""):
    out = {}
    for d, g in f.groupby("day"):
        g = g[g["minute"] >= 15]
        up = g[g["close" + sfx] > g["or15_high" + sfx]]
        dn = g[g["close" + sfx] < g["or15_low" + sfx]]
        tu = up["minute"].iloc[0] if len(up) else 999
        td = dn["minute"].iloc[0] if len(dn) else 999
        out[d] = (1 if tu < td else -1 if td < tu else 0, min(tu, td))
    return out
fb_r, fb_p = first_break(j), first_break(j, "_p")
agree = np.mean([fb_r[d][0] == fb_p[d][0] for d in fb_r])
tdiff = np.median([abs(fb_r[d][1] - fb_p[d][1]) for d in fb_r if fb_r[d][0] == fb_p[d][0] != 0])
md = ["# Proxy check: Dukascopy S&P 500 (SPY-scaled) vs real SPY (Yahoo), 9:30-11:00", "",
      f"{len(days)} sessions, {days[0]} .. {days[-1]}, {len(j):,} matched minutes.", "",
      "| measure | value |", "|---|---|",
      f"| correlation of 1-minute returns | {corr1:.3f} |",
      f"| correlation of 5-minute returns | {corr5:.3f} |",
      f"| proxy level vs SPY, median / 90th pct of abs gap (bp) | {lvl.median():+.1f} / {lvl.abs().quantile(.9):.1f} |",
      f"| median ratio of 1-minute high-low ranges (proxy / SPY) | {rng:.2f} |",
      f"| days where the first 15-minute range break has the same direction | {agree:.0%} |",
      f"| median timing difference of that break when both agree (minutes) | {tdiff:.0f} |", "",
      "Limits: one month only (Yahoo keeps ~30 days of 1-minute bars); the proxy is an index CFD bid feed;",
      "volume is not comparable. Strategy conclusions on the proxy must be confirmed on real SPY bars",
      "(paper trading on Yahoo's live 1-minute bars) before any money is involved."]
(ROOT / "reports" / "proxy_vs_spy.md").write_text("\n".join(md) + "\n")
print("\n".join(md))
