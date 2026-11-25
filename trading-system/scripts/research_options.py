"""In-sample options study (MODELED prices; see options/overlay.py for what is and is not modeled).

Underlying signals: the families with the best pre-cost edge, at parameters chosen on the full
2010-2019 in-sample period (so this estimate is optimistic; the 2020+ holdout is the real test).
For each: calls for longs, puts for shorts, an 8-point options grid (|delta| 0.5 / 0.3, nearest
expiry with min DTE 0 / 1, no premium stop / 50% premium stop), three cost scenarios, walk-forward
selection by year on the unfavorable scenario. Writes reports/research/options_summary.md.
"""
import datetime as dt
import itertools
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tradesys.analytics.metrics import summary  # noqa: E402
from tradesys.backtesting.engine import Backtester  # noqa: E402
from tradesys.backtesting.research import Study, holm  # noqa: E402
from tradesys.config import load_config  # noqa: E402
from tradesys.data_ingestion.free_sources import spy_equivalent  # noqa: E402
from tradesys.options.overlay import OptionParams, overlay  # noqa: E402
from tradesys.strategies.library import make  # noqa: E402

UNDERLYING = [("first_candle", {"candle_min": 5, "target_r": 3.0}), ("vol_breakout", {"k": 0.35}),
              ("gap_go", {"min_gap": 0.003, "candle_min": 5}), ("gap_go", {"min_gap": 0.005, "candle_min": 5}),
              ("oops", {"min_gap": 0.0, "target_r": None}), ("nr_orb", {"nr": 4, "minutes": 5, "stop": "far", "target_r": None}),
              ("gap_fill", {"min_gap": 0.002, "max_gap": 0.01, "entry_minute": 4}),
              ("vwap_reversion", {"stretch_atr": 3.5, "rsi_extreme": 20.0, "stop_atr": 2.0}),
              ("orb", {"minutes": 5, "stop": "far", "target_r": None}), ("noise_momentum", {"mult": 1.5, "every": 30})]
OPT_GRID = [dict(zip(("delta", "min_dte", "premium_stop"), v)) for v in itertools.product([0.5, 0.3], [0, 1], [None, 0.5])]

cfg = load_config(ROOT / "config" / "proxy.yaml")
yd = ROOT / "data" / "raw" / "yahoo"
spy, spx = pd.read_parquet(yd / "SPY_1d.parquet"), pd.read_parquet(yd / "GSPC_1d.parquet")
spy.index, spx.index = [d.date() for d in spy.index], [d.date() for d in spx.index]
vix = pd.read_parquet(yd / "VIX_1d.parquet")["close"]
vix.index = [d.date() for d in vix.index]
raw = pd.read_parquet(ROOT / "data" / "raw" / "oanda" / "SPX500_USD_1m.parquet")
raw = raw[raw.index >= "2009-11-01"]
st = Study(cfg, spy_equivalent(raw, spy, spx), "oanda-options-MODELED", vix=vix)
by_day = {d: g for d, g in st.feats.groupby("day")}
lines = ["# In-sample options study (MODELED option prices)", "",
         f"{st.days[0]} .. {st.days[-1]}, {len(st.days)} sessions. Black-Scholes on VIX x 1.15 with skew, the historical",
         "SPY expiry calendar, spreads scaled to premium, $0.69 a contract a side. Underlying parameters were chosen",
         "in-sample on the whole period, so these figures are optimistic. Not verified options data.", ""]
pv, log = {}, ROOT / "reports" / "research" / "trial_log.jsonl"
for name, up in UNDERLYING:
    fam = f"opt_{name}_{json.dumps(up, sort_keys=True)}"
    base = Backtester(cfg, make(name, **up), "normal").run(None, st.ctx, st.days, prepared=st.prep).trades
    rows = []
    for op in OPT_GRID:
        for sc in ("normal", "unfavorable", "extreme"):
            o = overlay(base, by_day, st.ctx, OptionParams(**op), sc)
            daily = pd.Series(0.0, index=st.days)
            if len(o):
                daily = daily.add(o.groupby("day")["net"].sum(), fill_value=0.0)
            key = (fam, json.dumps(op, sort_keys=True), sc)
            st.daily[key], st.trades[key] = daily, o
            s = summary(daily, o, cfg.risk.starting_equity) if len(o) else {"trades": 0, "net_profit": 0.0, "sharpe": float("nan")}
            rec = {"source": "oanda-options-MODELED", "strategy": fam, "params": op, "scenario": sc, "trades": s.get("trades"),
                   "net_profit": s.get("net_profit"), "sharpe": s.get("sharpe"), "win_rate": s.get("win_rate"),
                   "total_costs": float((o["frictionless"] - o["net"]).sum()) if len(o) else 0.0,
                   "frictionless": float(o["frictionless"].sum()) if len(o) else 0.0,
                   "logged_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
            with log.open("a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
            rows.append(rec)
    w = st.walk_forward(fam)
    pv[fam] = w["unfavorable"]["p_mean_le_0"]
    tab = pd.DataFrame(rows)
    best = tab[tab.scenario == "normal"].sort_values("sharpe", ascending=False).head(3)
    lines += [f"## {name} {json.dumps(up)}", "", "| options | trades | net $ | Sharpe | win % | no-cost $ |", "|---|---|---|---|---|---|"]
    for _, r in best.iterrows():
        lines.append(f"| `{json.dumps(r.params)}` | {r.trades} | {r.net_profit:,.0f} | {r.sharpe:.2f} | {(r.win_rate or 0):.0%} | {r.frictionless:,.0f} |")
    lines += ["", "Walk-forward: " + ", ".join(f"{sc} ${w[sc]['net_profit']:,.0f} (Sharpe {w[sc]['sharpe']:.2f}, {w[sc]['trades']} trades)"
                                             for sc in ("normal", "unfavorable", "extreme")), ""]
    print(name, up, {sc: (round(w[sc]["net_profit"]), round(w[sc]["sharpe"], 2)) for sc in ("normal", "unfavorable")},
          "| best normal:", best.iloc[0].params, round(best.iloc[0].net_profit), round(best.iloc[0].sharpe, 2), flush=True)
adj = holm(pv)
lines += ["## Holm correction across the options families (walk-forward, unfavorable)", "", "| family | p | Holm p |", "|---|---|---|"]
lines += [f"| {k} | {pv[k]:.3f} | {adj[k]:.3f} |" for k in pv]
(ROOT / "reports" / "research" / "options_summary.md").write_text("\n".join(lines) + "\n")
print("saved")
