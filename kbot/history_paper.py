"""Paper-trade the whole history month by month, as if the bot had run live.

Before each month, the model is retrained on every market that settled
before that month started (nothing from the month itself), then trades the
month with the live rules: buy the side and TP/SL pair with the best
predicted P&L if it clears the margin, at most one new entry per 5 minutes,
fills at the ask + 1c, exits by watching the bid each minute, Kalshi fees
both ways, 10 contracts.

  python -m kbot.history_paper                    # everything cached
  python -m kbot.history_paper --train-months 2   # months used before the first test month

Writes reports/history_paper.json and reports/history_paper_trades.csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .experiments import KEYS, Exp, add_real_labels, dataset

RULES = {
    "live: <=25c, margin 1c": dict(max_price=0.25, margin=0.01),
    "live + brake (30/day, pause if last 30 lose > $30)": dict(max_price=0.25, margin=0.01, brake=(30, 30, -30.0)),
    "<=25c, margin 2c": dict(max_price=0.25, margin=0.02),
    "any price, margin 1c": dict(max_price=1.0, margin=0.01),
    "any price, margin 2c": dict(max_price=1.0, margin=0.02),
}


def month_of(ts):
    d = datetime.fromtimestamp(int(ts), timezone.utc)
    return f"{d.year}-{d.month:02d}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=0, help="0 = everything cached")
    ap.add_argument("--train-months", type=int, default=2)
    ap.add_argument("--quote-fills", action="store_true",
                    help="old scoring: fill at the 1-minute quote without checking real trades")
    a = ap.parse_args(argv)
    t0 = time.time()
    days = a.days or (time.time() - 1765411200) / 86400 + 2      # series starts 2025-12-11
    d, ws = dataset(days)
    n = d["n_windows"]
    close = np.array([w.close_ts for w in ws[:n]])
    months = sorted({month_of(t) for t in close})
    # window index where each month starts
    starts = [int(np.searchsorted(close, datetime(int(m[:4]), int(m[5:]), 1, tzinfo=timezone.utc).timestamp()))
              for m in months]
    test_months = months[a.train_months:]
    edges = starts[a.train_months:] + [n]
    print(f"{n} markets {months[0]} .. {months[-1]}, {len(d['W'])} entry moments; "
          f"first {a.train_months} months train only, then {len(test_months)} months traded "
          f"[{time.time() - t0:.0f}s]\n", flush=True)
    e = Exp(d, n)
    e.edges, e.ts = edges, edges[0]
    Y = d["Y"]
    if not a.quote_fills:
        Y, have = add_real_labels(d, ws[:n])
        print(f"real-fill outcomes for {have}/{len(d['W'])} entry moments "
              f"(fills must match a real trade in the next minute)", flush=True)
        ok = {k: ~np.isnan(Y[k]) for k in KEYS}
    pred = e.walk(Y, train_mask=None if a.quote_fills else ok)
    print(f"models trained [{time.time() - t0:.0f}s]\n")
    out = {"months": months, "test_months": test_months, "rules": {}}
    for name, r in RULES.items():
        allow = {k: (e.ask(k.split("_")[0]) <= r["max_price"]) & ~np.isnan(Y[k]) for k in KEYS}
        pnl, info = e.trade(pred, Y, r["margin"], allow, r.get("brake"))
        by_m = defaultdict(list)
        for (i, k), p in zip(info, pnl):
            by_m[month_of(d["T"][i])].append(p)
        eq = np.cumsum(pnl)
        dd = float((np.maximum.accumulate(np.concatenate([[0], eq])) - np.concatenate([[0], eq])).max())
        top = np.sort(pnl)[::-1]
        out["rules"][name] = {
            "trades": len(pnl), "pnl": round(float(pnl.sum()), 2), "win_rate": round(float(np.mean(pnl > 0)), 3),
            "max_drawdown": round(dd, 2), "pnl_without_top10": round(float(top[10:].sum()), 2),
            "months": {m: {"trades": len(by_m[m]), "pnl": round(float(sum(by_m[m])), 2),
                           "win_rate": round(float(np.mean(np.array(by_m[m]) > 0)), 3) if by_m[m] else None}
                       for m in test_months}}
        print(f"== {name} ==")
        print(f"  {'month':8} {'trades':>6} {'win%':>6} {'P&L $':>9}")
        for m in test_months:
            v = np.array(by_m[m])
            print(f"  {m:8} {len(v):6d} {np.mean(v > 0) if len(v) else 0:6.1%} {v.sum():9.2f}")
        pos = sum(sum(by_m[m]) > 0 for m in test_months)
        print(f"  TOTAL    {len(pnl):6d} {np.mean(pnl > 0):6.1%} {pnl.sum():9.2f}   "
              f"months positive {pos}/{len(test_months)}, worst drawdown ${dd:.2f}, "
              f"without 10 best trades ${top[10:].sum():.2f}\n", flush=True)
        if name.startswith("live"):
            Path("reports").mkdir(exist_ok=True)
            with open("reports/history_paper_trades.csv", "w", newline="") as fh:
                wr = csv.writer(fh)
                wr.writerow(["time_utc", "ticker", "side", "take_profit", "stop_loss", "entry_ask",
                             "predicted_pnl_per_contract", "pnl_usd_10_contracts"])
                for (i, k), p in zip(info, pnl):
                    s, tp, sl = k.split("_")
                    w = ws[int(d["W"][i])]
                    wr.writerow([datetime.fromtimestamp(int(d["T"][i]), timezone.utc).isoformat(), w.ticker,
                                 "UP" if s == "up" else "DOWN", tp, sl, round(float(e.ask(s)[i]), 4),
                                 round(float(pred[k][i]), 4), round(float(p), 2)])
    Path("reports/history_paper.json").write_text(json.dumps(out, indent=1))
    print(f"saved reports/history_paper.json and reports/history_paper_trades.csv [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
