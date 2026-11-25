"""Record real SPY option chains (Yahoo, ~15 min delayed) and score the pre-declared options rules
on them (src/tradesys/options/real_quotes.py). PAPER / research only: nothing is ever ordered.

  python scripts/record_options.py --minutes 115              # sessions, 9:40-11:20 New York
  python scripts/record_options.py --minutes 115 --all-day    # whole session (calibration data)
  python scripts/record_options.py --evaluate 2026-11-27      # (re)score one recorded day

Quotes lag ~15 minutes, so recording 9:40-11:20 captures quotes stamped ~9:25-11:05, which covers
the 9:30-11:00 trading window. After 11:20 the day is scored once against the shadow share trades
the paper engine recorded (reports/paper/trades.jsonl) and the condor rules.
Raw quotes: data/recorded/yahoo_options/SPY_<day>/<HHMMSS>.parquet (gitignored: market data).
Results:    reports/paper/options_real.jsonl, reports/paper/options_calibration.jsonl (committed).
"""
import argparse
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tradesys.data_ingestion.free_sources import yahoo_daily, yahoo_spy_minutes  # noqa: E402
from tradesys.data_ingestion.yahoo_options import YahooOptions, snapshot  # noqa: E402
from tradesys.market_calendar import NY, is_session, session_close  # noqa: E402
from tradesys.options.real_quotes import calibration, evaluate_day  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
REC = ROOT / "data" / "recorded" / "yahoo_options"
OUT = ROOT / "reports" / "paper"


def day_dir(day: dt.date) -> Path:
    return REC / f"SPY_{day}"


def load_day(day: dt.date) -> pd.DataFrame:
    fs = sorted(day_dir(day).glob("*.parquet"))
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()


def evaluated(day: dt.date) -> bool:
    f = OUT / "options_calibration.jsonl"
    return f.exists() and any(json.loads(l).get("day") == str(day) for l in f.read_text().splitlines() if l.strip())


def evaluate(day: dt.date, rewrite: bool = False) -> None:
    q = load_day(day)
    if q.empty:
        print(json.dumps({"day": str(day), "options_real": "no recorded quotes"}), flush=True)
        return
    win_end = pd.Timestamp(dt.datetime.combine(day, dt.time(11, 0)), tz=NY)
    if not (q["quote_ts"].dropna() <= win_end).any():
        rec = {"day": str(day), "skip": "no quotes recorded in the 9:30-11:00 window",
               "first_quote": str(q["quote_ts"].min())}
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / "options_calibration.jsonl").open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(json.dumps(rec), flush=True)
        return
    b = yahoo_spy_minutes(7)
    b = b[(b.index.second == 0) & (b.index.tz_convert(NY).date == day)][["open", "high", "low", "close", "volume"]].astype(float)
    trades = []
    tf = OUT / "trades.jsonl"
    if tf.exists():
        trades = [t for t in map(json.loads, tf.read_text().splitlines()) if t.get("day") == str(day)]
    v = yahoo_daily("^VIX")["close"]
    prev = v[[pd.Timestamp(i).date() < day for i in v.index]]
    vix_prev = float(prev.iloc[-1]) if len(prev) else None
    rows = evaluate_day(day, q, b, trades, budget=250.0)
    cal = calibration(day, q, b, vix_prev)
    OUT.mkdir(parents=True, exist_ok=True)
    if rewrite:
        for name in ("options_real.jsonl", "options_calibration.jsonl"):
            f = OUT / name
            if f.exists():
                keep = [l for l in f.read_text().splitlines() if l.strip() and json.loads(l).get("day") != str(day)]
                f.write_text("".join(k + "\n" for k in keep))
    with (OUT / "options_real.jsonl").open("a") as fh:
        for r in rows:
            fh.write(json.dumps({"mode": "PAPER, REAL delayed quotes", **r}, default=str) + "\n")
    with (OUT / "options_calibration.jsonl").open("a") as fh:
        fh.write(json.dumps(cal, default=str) + "\n")
    done = [r for r in rows if "net" in r]
    by = {}
    for r in done:
        by.setdefault(r["rule"], []).append(r["net"])
    print(json.dumps({"day": str(day), "options_real_summary": {k: {"n": len(x), "net": round(sum(x), 2)} for k, x in by.items()},
                      "skipped": sum("skip" in r for r in rows), "calibration": cal}, default=str), flush=True)


def run(minutes: float, all_day: bool) -> None:
    until = time.time() + minutes * 60
    yo = YahooOptions("SPY", cache=ROOT / "state" / "yahoo_session.json")
    while time.time() < until:
        now = pd.Timestamp.now(tz=NY)
        day = now.date()
        if not is_session(day):
            time.sleep(300)
            continue
        start = pd.Timestamp(dt.datetime.combine(day, dt.time(9, 40)), tz=NY)
        end = pd.Timestamp(dt.datetime.combine(day, dt.time(11, 20)), tz=NY)
        if all_day:
            close = session_close(day)
            end = pd.Timestamp(dt.datetime.combine(day, close), tz=NY) + pd.Timedelta(minutes=16)
        if now < start:
            time.sleep(min(300.0, max(5.0, (start - now).total_seconds())))
            continue
        if now >= end:
            if not evaluated(day) and now >= pd.Timestamp(dt.datetime.combine(day, dt.time(11, 20)), tz=NY):
                try:
                    evaluate(day)
                except Exception as e:
                    print(json.dumps({"day": str(day), "evaluate_error": f"{type(e).__name__}: {e}"}), flush=True)
                    time.sleep(120)
                    continue
            time.sleep(300)
            continue
        try:
            snap = snapshot(yo, day)
            d = day_dir(day)
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f".{now:%H%M%S}.tmp"
            snap.to_parquet(tmp, index=False)
            tmp.rename(d / f"{now:%H%M%S}.parquet")
            print(json.dumps({"ts": now.isoformat(timespec="seconds"), "rows": len(snap),
                              "expiries": sorted({str(e) for e in snap["expiry"]}),
                              "quote_ts": str(snap["quote_ts"].iloc[0].tz_convert(NY)), "delay_s": round(float(snap["delay_s"].iloc[0]))}),
                  flush=True)
        except Exception as e:
            print(json.dumps({"ts": now.isoformat(timespec="seconds"), "error": f"{type(e).__name__}: {e}"}), flush=True)
        nxt = pd.Timestamp.now(tz="UTC").floor("min") + pd.Timedelta(minutes=1, seconds=20)
        time.sleep(max(1.0, (nxt - pd.Timestamp.now(tz="UTC")).total_seconds()))


ap = argparse.ArgumentParser()
ap.add_argument("--minutes", type=float, default=115)
ap.add_argument("--all-day", action="store_true")
ap.add_argument("--evaluate")
a = ap.parse_args()
if a.evaluate:
    evaluate(dt.date.fromisoformat(a.evaluate), rewrite=True)
else:
    run(a.minutes, a.all_day)
