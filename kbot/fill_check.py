"""Were the backtest's fills real? Check every trade against Kalshi's trade tape.

The backtest buys at the 1-minute candle's closing ask and sells at its
closing bid. In fast moves that quote can be stale: the ask shows 6c but
everyone who wanted it already took it. For each walk-forward trade (live
rules), this asks the trade tape:

  entry  did anyone actually BUY this side at <= our price within 30 s?
  exit   did anyone actually SELL this side at >= our price within 30 s?

and re-scores the P&L counting only trades that could have happened.

  python -m kbot.fill_check --days 126
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from .data import KALSHI, MaxyClient, _ts
from .experiments import KEYS, Exp, add_delayed_labels, dataset, quotes
from .strategy import taker_fee
from .trader import QTY

SLIP = 0.01


def tape(client, ticker, a, b, pages=3):
    out, cursor = [], None
    for _ in range(pages):
        p = {"ticker": ticker, "min_ts": a, "max_ts": b, "limit": 1000}
        if cursor:
            p["cursor"] = cursor
        try:
            d = client._get(f"{KALSHI}/markets/trades", p, retries=2)
        except Exception:
            return None
        out += d.get("trades") or []
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def side_price(t, side):
    return float(t["yes_price_dollars"]) if side == "up" else float(t["no_price_dollars"])


def exit_of(q, i, side, tp, sl):
    """Replay barrier_pnl: (exit index or None if settled, raw bid at exit)."""
    yb, ya = q[i][1], q[i][2]
    entry = (ya if side == "up" else round(1 - yb, 4)) + SLIP
    last = None
    for j in range(i + 1, len(q)):
        raw = q[j][1] if side == "up" else round(1 - q[j][2], 4)
        b = raw - SLIP
        last = (j, raw)
        if b >= entry + tp or b <= entry - sl:
            return j, raw
    return last if last else (None, None)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=126)
    ap.add_argument("--margin", type=float, default=0.01)
    ap.add_argument("--max-price", type=float, default=0.25)
    ap.add_argument("--any-side", action="store_true",
                    help="count a trade by either side as proof of a fill (a limit order is filled "
                         "by sellers hitting it too), not only takers on our side")
    ap.add_argument("--window", type=int, default=30, help="seconds after the decision to look for trades")
    ap.add_argument("--delay", type=int, default=0, help="fill this many minutes after the decision "
                    "(model trained on delayed fills)")
    a = ap.parse_args(argv)
    d, ws = dataset(a.days)
    e = Exp(d, d["n_windows"])
    Y = d["Y"]
    if a.delay:
        Y, _ = add_delayed_labels(d, ws, a.delay)
    pred = e.walk(Y)
    allow = {k: e.ask(k.split("_")[0]) <= a.max_price for k in KEYS}
    pnl, info = e.trade(pred, Y, a.margin, allow)
    print(f"{len(pnl)} walk-forward trades (<= {a.max_price * 100:.0f}c, margin {a.margin}), "
          f"backtest P&L ${pnl.sum():.2f}; checking each against the trade tape...", flush=True)
    client = MaxyClient("BTC", min_interval=0.25)
    rows = []
    for n, ((i, k), p) in enumerate(zip(info, pnl)):
        w = ws[int(d["W"][i])]
        q = quotes(w)
        t = int(d["T"][i])
        qi = next((j for j, qq in enumerate(q) if qq[0] == t), None)
        side, tp, sl = k.split("_")
        tp, sl = float(tp), float(sl)
        if qi is not None and a.delay:
            qi += a.delay                       # the fill happens at this later minute
            t = q[qi][0]
        ask = float(q[qi][2] if side == "up" else 1 - q[qi][1]) if qi is not None else float(e.ask(side)[i])
        tr = tape(client, w.ticker, t, t + a.window)
        if tr is None or qi is None:
            continue
        buys = [side_price(x, side) for x in tr
                if a.any_side or x.get("taker_side") == ("yes" if side == "up" else "no")]
        entry_ok = any(px <= ask + 0.01 for px in buys)
        j, raw = exit_of(q, qi, side, tp, sl)
        exit_ok, exit_real = None, None
        if j is not None:
            te = q[j][0]
            tr2 = tape(client, w.ticker, te, te + a.window)
            if tr2 is not None:
                sells = [side_price(x, side) for x in tr2
                         if a.any_side or x.get("taker_side") == ("no" if side == "up" else "yes")]
                exit_ok = any(px >= raw - 0.01 for px in sells)
                exit_real = max(sells) if sells else None
        won = (w.result == "yes") == (side == "up")
        rows.append({"pnl": float(p), "ask": ask, "entry_ok": entry_ok, "exit_ok": exit_ok,
                     "exit_raw": raw, "exit_real": exit_real, "settled": j is None, "won": won,
                     "entry": ask + SLIP, "ticker": w.ticker, "t": t, "pair": k,
                     "cheapest_real_buy": min(buys) if buys else None})
        if (n + 1) % 100 == 0:
            print(f"  {n + 1}/{len(pnl)}", flush=True)
    R = rows
    tot = sum(r["pnl"] for r in R)
    ok_in = [r for r in R if r["entry_ok"]]
    ok_both = [r for r in ok_in if r["settled"] or r["exit_ok"]]
    print(f"\n{len(R)} trades checked, backtest P&L ${tot:.2f}")
    print(f"  entry really fillable:          {len(ok_in):4d} ({len(ok_in) / len(R):.0%})  P&L ${sum(r['pnl'] for r in ok_in):8.2f}")
    print(f"  entry AND exit fillable:        {len(ok_both):4d} ({len(ok_both) / len(R):.0%})  P&L ${sum(r['pnl'] for r in ok_both):8.2f}")
    bad = [r for r in R if not r["entry_ok"]]
    print(f"  entries with NO real trade:     {len(bad):4d}            P&L ${sum(r['pnl'] for r in bad):8.2f}  (backtest profit that likely wasn't real)")
    # realistic version: fillable entries; exits that weren't fillable sell at the best real sell price
    real = []
    for r in ok_in:
        if r["settled"] or r["exit_ok"]:
            real.append(r["pnl"])
        elif r["exit_real"] is not None:
            px = r["exit_real"]
            px -= SLIP
            real.append((px - r["entry"] - taker_fee(min(r["entry"], .99), QTY)
                         - taker_fee(max(px, .01), QTY)) * QTY)
        else:   # no seller at all: hold to settlement
            real.append(((1.0 if r["won"] else 0.0) - r["entry"] - taker_fee(min(r["entry"], .99), QTY)) * QTY)
    print(f"  REALISTIC (fillable entries, unfillable exits at the real price): ${sum(real):.2f}")
    by = defaultdict(list)
    for r in R:
        band = "<10c" if r["ask"] < .10 else "10-25c"
        by[(band, r["entry_ok"])].append(r["pnl"])
    print("\n  by entry price:")
    for (band, ok), v in sorted(by.items()):
        print(f"    {band:7} {'fillable  ' if ok else 'NOT filled'}  {len(v):4d} trades  P&L ${sum(v):8.2f}")
    Path("reports").mkdir(exist_ok=True)
    out = (f"reports/fill_check{'_delay' + str(a.delay) if a.delay else ''}"
           f"{'_any' if a.any_side else ''}{'_w' + str(a.window) if a.window != 30 else ''}.json")
    Path(out).write_text(json.dumps(R, indent=0))
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
