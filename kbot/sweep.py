"""Compare strategy variants on already-cached windows (no downloads).

  python -m kbot.sweep --days 14
"""
from __future__ import annotations

import argparse
import gzip
import json
import time
from pathlib import Path

from . import backtest
from .data import Window
from .memory import Memory
from .model import Calibrator
from .strategy import Config

VARIANTS = {
    "baseline (rapid exit)": dict(),
    "hold to settlement": dict(hold=True),
    "hold, no stop": dict(hold=True, stop_p=0.0),
    "rapid, take-profit 8c": dict(take_profit=0.08),
    "rapid, P>=0.92": dict(min_p=0.92),
    "hold, P>=0.92": dict(hold=True, min_p=0.92),
    "hold, last 5 min only": dict(hold=True, only_last=300),
    "hold, last 3 min only": dict(hold=True, only_last=180),
    "value: P>=0.6, EV>=10%, hold": dict(hold=True, min_p=0.60, min_ev=0.10, stop_p=0.0),
}


def load_cached(coin: str, days: float, cache="data/cache", compact: bool = True) -> list[Window]:
    since = time.time() - days * 86400
    out = []
    for p in Path(cache, coin).glob("*.json.gz"):
        try:
            with gzip.open(p, "rt") as fh:
                w = Window.from_json(json.load(fh), compact=compact)
        except (OSError, EOFError, ValueError, TypeError):
            continue                     # partially written by a running download
        if w.close_ts >= since:
            out.append(w)
    return sorted(out, key=lambda w: w.close_ts)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", default="BTC")
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--slippage", type=float, default=0.01)
    a = ap.parse_args(argv)
    wins = load_cached(a.coin, a.days)
    print(f"{len(wins)} cached windows\n")
    print(f"{'variant':32} {'trades':>6} {'win%':>6} {'P&L $':>9} {'ROI':>7} {'maxDD $':>8} {'2nd half $':>10}")
    rows = {}
    for name, kw in VARIANTS.items():
        cfg = Config(**kw)
        mem = Memory(base_threshold=cfg.min_p)
        s = backtest.run(wins, Calibrator(), mem, cfg, a.slippage)
        tr = s.pop("_trades")
        half = sum(t.pnl for t in tr[len(tr) // 2:])
        print(f"{name:32} {s['trades']:>6} {100 * (s['win_rate'] or 0):>5.1f}% "
              f"{s['total_pnl_usd']:>9.2f} {100 * (s['roi_on_capital_deployed'] or 0):>6.1f}% "
              f"{s['max_drawdown_usd']:>8.2f} {half:>10.2f}")
        rows[name] = {k: s[k] for k in ("trades", "win_rate", "total_pnl_usd",
                                        "roi_on_capital_deployed", "max_drawdown_usd", "by_exit")}
    Path("reports").mkdir(exist_ok=True)
    Path("reports/sweep.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
