"""INFORMATIONAL ONLY, outside the brief's 9:30-11:00 trading window: the same in-sample study with
positions allowed until the close (entries to 15:30, flatten 15:55). Measures what the 11:00 limit
costs the published all-day strategies; nothing here may be traded without the user's approval."""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tradesys.backtesting.research import Study  # noqa: E402
from tradesys.config import load_config  # noqa: E402
from tradesys.data_ingestion.free_sources import spy_equivalent  # noqa: E402

cfg = load_config(ROOT / "config" / "proxy.yaml", {"session": {"entry_cutoff": "15:30", "flatten_start": "15:55",
                                                                 "hard_flat": "16:00"}})
yd = ROOT / "data" / "raw" / "yahoo"
spy, spx = pd.read_parquet(yd / "SPY_1d.parquet"), pd.read_parquet(yd / "GSPC_1d.parquet")
spy.index, spx.index = [d.date() for d in spy.index], [d.date() for d in spx.index]
vix = pd.read_parquet(yd / "VIX_1d.parquet")["close"]
vix.index = [d.date() for d in vix.index]
raw = pd.read_parquet(ROOT / "data" / "raw" / "oanda" / "SPX500_USD_1m.parquet")
raw = raw[raw.index >= "2009-11-01"]
st = Study(cfg, spy_equivalent(raw, spy, spx), "oanda-FULLDAY-informational", vix=vix)
grids = {"noise_momentum": [{"mult": 1.0, "every": 30}, {"mult": 1.5, "every": 30}],
         "first_candle": [{"candle_min": 5, "target_r": 10.0}, {"candle_min": 5, "target_r": None}],
         "gap_go": [{"min_gap": 0.005, "candle_min": 5}]}
out = []
for name, pl in grids.items():
    tab = st.run(name, params_list=pl)
    for _, r in tab.iterrows():
        k = (name, json.dumps(r.params, sort_keys=True), r.scenario)
        tr = st.trades[k]
        fr = float((tr["frictionless"]).sum()) if len(tr) else 0.0
        by = st.daily[k].groupby([d.year for d in st.daily[k].index]).sum()
        out.append({"strategy": name, "params": r.params, "scenario": r.scenario, "trades": r.trades,
                    "net": round(r.net_profit), "sharpe": round(r.sharpe, 2), "frictionless": round(fr),
                    "years_positive": f"{int((by > 0).sum())}/{len(by)}"})
        print(out[-1], flush=True)
(ROOT / "reports" / "research" / "fullday_informational.json").write_text(json.dumps(out, indent=1, default=str))
