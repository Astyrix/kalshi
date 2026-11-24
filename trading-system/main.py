"""tradesys command line.

  python main.py data-coverage                          # Databento dataset ranges and schemas (free metadata)
  python main.py data-quote --start 2018-05-01 --end 2026-11-01            # what a download would cost
  python main.py data-fetch --start 2018-05-01 --end 2019-01-01 --approve-usd 5
  python main.py data-report --start 2018-05-01 --end 2026-11-01           # data-quality report
  python main.py backtest --strategy orb --params '{"minutes": 15}' --start 2018-05-01 --end 2019-12-31
  python main.py freeze --strategy orb --params '{"minutes": 15}' --note "chosen in-sample"
  python main.py backtest --frozen config/frozen/orb-XXXX.json --start 2020-01-01 --end 2026-11-01
  python main.py kill-switch --status | --trip "reason" | --reset --by NAME
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from tradesys.config import load_config  # noqa: E402

log = logging.getLogger("tradesys")
EQUITY_SETS = ["XNAS.ITCH", "XNYS.PILLAR", "ARCX.PILLAR", "XBOS.ITCH", "XPSX.ITCH", "BATS.PITCH", "IEXG.TOPS",
               "DBEQ.BASIC", "EQUS.MINI", "EQUS.SUMMARY", "EQUS.PLUS", "EQUS.ALL", "OPRA.PILLAR"]


def cmd_coverage(a, cfg):
    from tradesys.data_ingestion.databento_source import coverage, save_json
    cov = coverage(a.datasets or EQUITY_SETS)
    save_json(ROOT / "reports" / "data_coverage.json", cov)
    for ds, v in cov.items():
        if "error" in v:
            print(f"{ds:14} ERROR {v['error'][:120]}")
        else:
            r = v["range"]
            print(f"{ds:14} {r.get('start', '?')[:10]} -> {r.get('end', '?')[:10]}  schemas: {', '.join(v['schemas'])}")


def cmd_quote(a, cfg):
    from tradesys.data_ingestion.databento_source import Request, quote
    db = cfg.data["databento"]
    ds, sym = a.dataset or db["equities_dataset"], a.symbol or db["symbol"]
    total = 0.0
    y0, y1 = int(a.start[:4]), int(a.end[:4])
    for y in range(y0, y1 + 1):
        s, e = max(a.start, f"{y}-01-01"), min(a.end, f"{y + 1}-01-01")
        if s >= e:
            continue
        q = quote(Request(ds, a.schema, (sym,), s, e))
        total += q["usd"]
        print(f"{ds} {a.schema} {sym} {s} -> {e}: ${q['usd']:.2f} ({q['bytes'] / 1e6:,.1f} MB billable)")
    print(f"total ${total:.2f}")


def cmd_fetch(a, cfg):
    from tradesys.data_ingestion.databento_source import SpendNotApproved, fetch_bars
    db = cfg.data["databento"]
    try:
        out = fetch_bars(a.dataset or db["equities_dataset"], a.symbol or db["symbol"], a.start, a.end,
                         a.approve_usd, ROOT / cfg.data.get("root", "data"), schema=a.schema, dry_run=a.dry_run)
    except SpendNotApproved as e:
        print("NOT DOWNLOADED:", e)
        return 2
    print(json.dumps(out, indent=1))


def cmd_report(a, cfg):
    from tradesys.data_ingestion.store import load_bars
    from tradesys.data_ingestion.validate import check, to_markdown
    db = cfg.data["databento"]
    ds, sym = a.dataset or db["equities_dataset"], a.symbol or db["symbol"]
    bars = load_bars(ROOT / "data", ds, sym, a.start, a.end, schema=a.schema)
    if bars.empty:
        print("no cached bars for that range: run data-fetch first")
        return 1
    rep = check(bars, dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end) - dt.timedelta(days=1))
    notes = [f"Source: Databento {ds} {a.schema} for {sym}. A single-venue feed holds only that venue's trades: "
             "bar volume is that venue's share, not consolidated volume; highs and lows can differ from the "
             "consolidated tape by a few cents.",
             "Bars are labelled by their start time (UTC from the vendor, converted to America/New_York)."]
    md = to_markdown(rep, f"Data quality: {ds} {sym} {a.schema}", notes)
    out = ROOT / "reports" / f"data_quality_{ds}_{sym}.md"
    out.write_text(md)
    print(md[:3000])
    print("saved", out)


def cmd_backtest(a, cfg):
    from tradesys.analytics.metrics import summary
    from tradesys.backtesting.engine import Backtester
    from tradesys.data_ingestion.store import load_bars
    from tradesys.feature_engineering.features import daily_context, intraday
    from tradesys.strategies.library import make
    oos0 = cfg.validation.out_of_sample[0]
    if a.frozen:
        spec = json.loads(Path(a.frozen).read_text())
        name, params = spec["strategy"], spec["params"]
        if spec["config_fingerprint"] != cfg.fingerprint():
            print("refused: the configuration changed since this spec was frozen")
            return 2
    else:
        name, params = a.strategy, json.loads(a.params or "{}")
        if dt.date.fromisoformat(a.end) > oos0:
            print(f"refused: {a.end} reaches the out-of-sample period (from {oos0}); freeze the strategy and pass --frozen")
            return 2
    db = cfg.data["databento"]
    start_load = (dt.date.fromisoformat(a.start) - dt.timedelta(days=45)).isoformat()   # warm-up for features
    bars = load_bars(ROOT / "data", a.dataset or db["equities_dataset"], db["symbol"], start_load, a.end)
    if bars.empty:
        print("no cached bars: run data-fetch first")
        return 1
    feats, ctx = intraday(bars), daily_context(bars)
    days = [d for d in sorted(set(feats["day"])) if dt.date.fromisoformat(a.start) <= d < dt.date.fromisoformat(a.end)]
    results = {}
    for sc in (["normal", "unfavorable", "extreme"] if a.scenario == "all" else [a.scenario]):
        res = Backtester(cfg, make(name, **params), sc).run(feats, ctx, days)
        results[sc] = summary(res.daily, res.trades, res.starting_equity)
        print(f"{name} {params} [{sc}]: " + ", ".join(f"{k} {v:.4g}" if isinstance(v, float) else f"{k} {v}"
                                                      for k, v in results[sc].items()))
    if a.frozen:
        from tradesys.backtesting.freeze import record_oos_run
        rec = record_oos_run(cfg, Path(a.frozen), results)
        if rec["contaminated"]:
            print("WARNING: this strategy family was run on the out-of-sample period before this spec was frozen: "
                  "the holdout is contaminated; validate on forward paper trading instead")


def cmd_freeze(a, cfg):
    from tradesys.backtesting.freeze import freeze
    print(freeze(cfg, a.strategy, json.loads(a.params or "{}"), a.note))


def cmd_kill(a, cfg):
    from tradesys.risk_management.risk import KillSwitch
    ks = KillSwitch(ROOT / cfg.risk.kill_switch_file)
    if a.trip:
        ks.trip(a.trip)
    if a.reset:
        if not a.by:
            print("--reset needs --by NAME")
            return 2
        ks.reset(a.by)
    print(json.dumps(ks.state(), indent=1))


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="tradesys: SPY 9:30-11:00 research system")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("data-coverage"); p.add_argument("--datasets", nargs="*")
    for nm in ("data-quote", "data-fetch", "data-report"):
        p = sub.add_parser(nm)
        p.add_argument("--dataset"); p.add_argument("--symbol"); p.add_argument("--schema", default="ohlcv-1m")
        p.add_argument("--start", required=True); p.add_argument("--end", required=True)
        if nm == "data-fetch":
            p.add_argument("--approve-usd", type=float, default=0.0)
            p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("backtest")
    p.add_argument("--strategy"); p.add_argument("--params"); p.add_argument("--frozen"); p.add_argument("--dataset")
    p.add_argument("--start", required=True); p.add_argument("--end", required=True)
    p.add_argument("--scenario", default="all", choices=["all", "normal", "unfavorable", "extreme"])
    p = sub.add_parser("freeze"); p.add_argument("--strategy", required=True); p.add_argument("--params")
    p.add_argument("--note", default="")
    p = sub.add_parser("kill-switch"); p.add_argument("--status", action="store_true"); p.add_argument("--trip")
    p.add_argument("--reset", action="store_true"); p.add_argument("--by")
    a = ap.parse_args(argv)
    cfg = load_config()
    fn = {"data-coverage": cmd_coverage, "data-quote": cmd_quote, "data-fetch": cmd_fetch, "data-report": cmd_report,
          "backtest": cmd_backtest, "freeze": cmd_freeze, "kill-switch": cmd_kill}[a.cmd]
    return fn(a, cfg) or 0


if __name__ == "__main__":
    raise SystemExit(main())
