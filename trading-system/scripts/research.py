"""In-sample study on one free proxy source.

  python scripts/research.py histdata            # HistData SPXUSD 2010-11 .. 2018-12
  python scripts/research.py oanda               # Oanda SPX500_USD 2010-01 .. 2019-12 (the primary in-sample source)

Writes reports/research/<source>_summary.md, <source>_walkforward.json and appends every
trial to reports/research/trial_log.jsonl. In-sample only: the out-of-sample period is cut off.
"""
import datetime as dt
import json
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tradesys.backtesting.research import GRIDS, NEEDS_VOLUME, Study, holm  # noqa: E402
from tradesys.config import load_config  # noqa: E402
from tradesys.data_ingestion.free_sources import duka_bars, spy_equivalent  # noqa: E402
from tradesys.data_ingestion.validate import check, to_markdown  # noqa: E402

logging.basicConfig(level=logging.WARNING)
src = sys.argv[1]
only = sys.argv[2].split(",") if len(sys.argv) > 2 else list(GRIDS)
cfg = load_config(ROOT / "config" / "proxy.yaml")
yd = ROOT / "data" / "raw" / "yahoo"
spy, spx = pd.read_parquet(yd / "SPY_1d.parquet"), pd.read_parquet(yd / "GSPC_1d.parquet")
spy.index, spx.index = [d.date() for d in spy.index], [d.date() for d in spx.index]
div = pd.read_parquet(yd / "SPY_dividends.parquet")["amount"]
div.index = [d.date() for d in div.index]
if src == "histdata":
    raw = pd.read_parquet(ROOT / "data" / "raw" / "histdata" / "SPXUSD_1m.parquet")
elif src == "oanda":
    raw = pd.read_parquet(ROOT / "data" / "raw" / "oanda" / "SPX500_USD_1m.parquet")
    raw = raw[raw.index >= "2009-11-01"]          # 2 months of warm-up before 2010
else:
    raw = duka_bars(ROOT / "data", dt.date(2011, 9, 19), dt.date(2019, 12, 31))
bars = spy_equivalent(raw, spy, spx)
out = ROOT / "reports" / "research"
out.mkdir(parents=True, exist_ok=True)
d0, d1 = bars.index[0].tz_convert("America/New_York").date(), bars.index[-1].tz_convert("America/New_York").date()
rep = check(bars, d0, d1)
(ROOT / "reports" / f"data_quality_{src}.md").write_text(to_markdown(rep, f"Data quality: {src} (SPY-equivalent proxy)", [
    "Index CFD prices scaled to SPY dollars with the previous session's SPY / S&P 500 close ratio.",
    "Volume is tick count (Dukascopy) or absent (HistData): zero-volume counts are expected and not errors.",
    "HistData timestamps are EST all year (no DST) and were shifted by +5 h to UTC before conversion to New York time."]))
print(f"{src}: {len(bars)} bars, {d0} .. {d1}; quality {rep.summary()}", flush=True)
st = Study(cfg, bars, src, dividends=div)
wf, pv = {}, {}
lines = [f"# In-sample study: {src} ({st.days[0]} .. {st.days[-1]}, {len(st.days)} sessions)", "",
         "Every grid point under three cost scenarios; walk-forward selection on the unfavorable scenario.",
         "Proxy data (S&P 500 index CFD scaled to SPY dollars). Not out-of-sample.", ""]
for name in only:
    if name in NEEDS_VOLUME and not st.has_volume:
        lines += [f"## {name}: skipped (needs volume; {src} has none)", ""]
        continue
    tab = st.run(name)
    w = st.walk_forward(name)
    wf[name], pv[name] = w, w["unfavorable"]["p_mean_le_0"]
    best = tab[tab.scenario == "normal"].sort_values("sharpe", ascending=False).head(5)
    lines += [f"## {name}", "", "Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):", "",
              "| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |", "|---|---|---|---|---|---|---|---|"]
    for _, r in best.iterrows():
        lines.append(f"| `{json.dumps(r.params, default=list)}` | {r.trades} | {r.net_profit:,.0f} | {r.sharpe:.2f} | "
                     f"{r.max_drawdown:.1%} | {(r.win_rate or 0):.0%} | {(r.profit_factor or 0):.2f} | {(r.expectancy_r or 0):+.3f} |")
    lines += ["", "Walk-forward (params chosen each year on earlier years only):", "",
              "| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |", "|---|---|---|---|---|---|---|"]
    for sc in ("normal", "unfavorable", "extreme"):
        x = w[sc]
        lines.append(f"| {sc} | {x['sessions']} | {x['trades']} | {x['net_profit']:,.0f} | {x['sharpe']:.2f} | "
                     f"[{x['sharpe_ci95'][0]:.2f}, {x['sharpe_ci95'][1]:.2f}] | {x['p_mean_le_0']:.3f} |")
    lines += ["", "By test year (unfavorable): " + ", ".join(f"{y}: {v:+,.0f}" for y, v in w["unfavorable"]["by_year"].items()), ""]
    print(name, {sc: (round(w[sc]["net_profit"]), round(w[sc]["sharpe"], 2), w[sc]["p_mean_le_0"]) for sc in ("normal", "unfavorable", "extreme")}, flush=True)
adj = holm(pv)
lines += ["## Families after a Holm correction (walk-forward, unfavorable costs)", "",
          "| family | p | Holm-adjusted p |", "|---|---|---|"] + [f"| {k} | {pv[k]:.3f} | {adj[k]:.3f} |" for k in pv]
tag = src if set(only) == set(GRIDS) else f"{src}_{'-'.join(only)}"      # a subset never overwrites the full study
(out / f"{tag}_summary.md").write_text("\n".join(lines) + "\n")
(out / f"{tag}_walkforward.json").write_text(json.dumps(wf, indent=1, default=str))
print("saved", out / f"{tag}_summary.md")
