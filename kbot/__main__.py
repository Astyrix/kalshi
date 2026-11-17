"""Command line:

  python -m kbot backtest --days 30          walk-forward backtest, learns as it goes
  python -m kbot live                        paper-trade the current 15-minute market
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import backtest, live
from .data import MaxyClient
from .memory import Memory
from .model import Calibrator
from .strategy import Config


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kbot")
    ap.add_argument("--coin", default="BTC")
    ap.add_argument("--state", default="state", help="folder for learned weights and memory")
    ap.add_argument("--min-p", type=float, default=0.85)
    ap.add_argument("--min-ev", type=float, default=0.035)
    ap.add_argument("--take-profit", type=float, default=0.04)
    ap.add_argument("--stop-p", type=float, default=0.60)
    ap.add_argument("--qty", type=int, default=10)
    ap.add_argument("--rate", type=float, default=0.3, help="min seconds between requests")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest")
    b.add_argument("--days", type=float, default=7)
    b.add_argument("--slippage", type=float, default=0.01)
    b.add_argument("--fresh", action="store_true", help="ignore saved weights and memory")
    b.add_argument("--log-decisions", action="store_true")

    l = sub.add_parser("live")
    l.add_argument("--interval", type=float, default=2.0)
    l.add_argument("--ticks", type=int)
    l.add_argument("--quiet-waits", action="store_true", help="only print ENTER/EXIT and fills")

    a = ap.parse_args(argv)
    state = Path(a.state); state.mkdir(exist_ok=True)
    cfg = Config(min_p=a.min_p, min_ev=a.min_ev, take_profit=a.take_profit,
                 stop_p=a.stop_p, qty=a.qty)
    cal_p, mem_p = state / f"calibrator-{a.coin}.json", state / f"memory-{a.coin}.json"
    client = MaxyClient(a.coin, min_interval=a.rate)

    if a.cmd == "backtest":
        cal = Calibrator() if a.fresh else Calibrator.load(cal_p)
        mem = Memory(base_threshold=a.min_p) if a.fresh else Memory.load(mem_p, a.min_p)
        wins = backtest.fetch_windows(client, a.days)
        reports = Path("reports")
        reports.mkdir(exist_ok=True)
        summ = backtest.run(wins, cal, mem, cfg, a.slippage,
                            reports / "decisions.jsonl" if a.log_decisions else None)
        out = backtest.write_reports(summ, reports)
        cal.save(cal_p); mem.save(mem_p)
        print(json.dumps(summ, indent=2))
        print(f"\nreport: {out}\nlearned state saved to {state}/")
    else:
        cal, mem = Calibrator.load(cal_p), Memory.load(mem_p, a.min_p)
        try:
            live.run(client, cal, mem, cfg, a.interval, a.ticks,
                     Path("reports") / "live.jsonl", a.quiet_waits)
        except KeyboardInterrupt:
            pass
        finally:
            mem.save(mem_p)


if __name__ == "__main__":
    main()
