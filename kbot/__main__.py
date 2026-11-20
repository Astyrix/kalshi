"""Command line:

  python -m kbot backtest --days 30          walk-forward backtest, learns as it goes
  python -m kbot live                        paper-trade the current 15-minute market (simple model)
  python -m kbot paper --margin 0.02         paper-trade with the day-trader scalp model
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
    ap.add_argument("--hold", action="store_true", help="hold to settlement unless the thesis breaks")
    ap.add_argument("--only-last", type=int, default=0, help="only enter with this many seconds or fewer left")
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

    pp = sub.add_parser("paper", help="paper-trade with the day-trader model (kbot/paper.py)")
    pp.add_argument("--model", default="state/trader-model.pkl")
    pp.add_argument("--margin", type=float, default=0.02, help="min predicted profit per contract to enter")
    pp.add_argument("--minutes", type=int, help="stop after this many minutes")
    pp.add_argument("--history-days", type=float, default=35)
    pp.add_argument("--retrain-hour", type=int, default=6, help="UTC hour to retrain daily (-1 = never)")
    sub.add_parser("paper-report", help="summarize paper trades and the live pattern scoreboard")

    a = ap.parse_args(argv)
    if a.cmd == "paper":
        from .paper import Paper
        Paper(a.model, a.margin, a.qty, a.history_days, Path(a.state), Path("reports"), a.rate,
              None if a.retrain_hour < 0 else a.retrain_hour).run(a.minutes)
        return
    if a.cmd == "paper-report":
        from .paper import report
        report(Path(a.state))
        return
    state = Path(a.state); state.mkdir(exist_ok=True)
    cfg = Config(min_p=a.min_p, min_ev=a.min_ev, take_profit=a.take_profit,
                 stop_p=a.stop_p, qty=a.qty, hold=a.hold, only_last=a.only_last)
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
