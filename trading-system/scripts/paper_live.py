"""Live PAPER trading on real SPY 1-minute bars (Yahoo), 9:30-11:00 New York. No broker, no orders.

  python scripts/paper_live.py --minutes 115           # run (sleeps outside the window)
  python scripts/paper_live.py --replay-today           # replay today's session so far, once

Strategies run in shadow mode: all six families at their default parameters until a frozen
spec exists, then also every spec in config/frozen/ (marked validated=false until the
out-of-sample run passes and the user approves).
"""
import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tradesys.config import load_config  # noqa: E402
from tradesys.execution.paper_live import PaperLive  # noqa: E402
from tradesys.strategies.library import REGISTRY  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
ap = argparse.ArgumentParser()
ap.add_argument("--minutes", type=float, default=115)
ap.add_argument("--replay-today", action="store_true")
a = ap.parse_args()
cfg = load_config()
specs = [{"name": n, "params": {}, "validated": False, "spec": None} for n in REGISTRY]
for f in sorted((ROOT / "config" / "frozen").glob("*.json")):
    if f.name == "oos_runs.jsonl":
        continue
    s = json.loads(f.read_text())
    specs.append({"name": s["strategy"], "params": s["params"], "validated": False, "spec": f.name})
pl = PaperLive(cfg, specs)
if a.replay_today:
    pl.step(pd.Timestamp.now(tz="UTC"), final=False)
else:
    pl.run(until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=a.minutes))
