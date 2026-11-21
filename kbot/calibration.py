"""Is the market price right? A model-free efficiency check, per coin.

For every minute of every settled market and each side, take the quoted ask
(only two-sided books at that minute; nothing from later minutes), and
compare how often that side actually won with what buying it would cost:
ask + 1c slippage + Kalshi's taker fee, held to settlement. A bucket only
counts as an edge if it is positive with z >= 3.

  python -m kbot.calibration --coins BTC,ETH,SOL,XRP --days 30
"""
from __future__ import annotations

import argparse
import math
from collections import defaultdict

import numpy as np

from .strategy import taker_fee
from .sweep import load_cached

PRICE = [(0.005, 0.05), (0.05, 0.15), (0.15, 0.35), (0.35, 0.65), (0.65, 0.85), (0.85, 0.95), (0.95, 0.995)]
LEFT = [(60, 180), (180, 420), (420, 900)]
# --fine: favourites late in the window, the style of the consistently winning
# Polymarket wallets (kbot.poly_study)
FINE_PRICE = [(0.80, 0.90), (0.90, 0.95), (0.95, 0.97), (0.97, 0.98), (0.98, 0.99), (0.99, 0.995)]
FINE_LEFT = [(60, 120), (120, 240), (240, 420), (420, 900)]


def run(coin, days, slip=0.01, qty=10, price=None, left=None):
    price, left_b = price or PRICE, left or LEFT
    ws = [w for w in load_cached(coin, days) if w.result in ("yes", "no")]
    g = defaultdict(list)          # (left bucket, price bucket) -> per-contract P&L
    spreads = []
    for w in ws:
        for t, yb, ya, _ in w.quotes:
            if yb is None or ya is None or not (0.005 < yb <= ya < 0.995) or t >= w.close_ts:
                continue
            left = w.close_ts - t
            lb = next((b for b in left_b if b[0] <= left < b[1]), None)
            if lb is None:
                continue
            spreads.append(ya - yb)
            for side, ask in (("up", ya), ("dn", round(1 - yb, 4))):
                pb = next((b for b in price if b[0] <= ask < b[1]), None)
                if pb is None:
                    continue
                won = (w.result == "yes") == (side == "up")
                cost = ask + slip + taker_fee(min(ask + slip, 0.99), qty)
                g[(lb, pb)].append((1.0 if won else 0.0) - cost)
    return ws, g, np.array(spreads)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default="BTC,ETH,SOL,XRP")
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--slip", type=float, default=0.01, help="paid above the ask")
    ap.add_argument("--qty", type=int, default=10, help="contracts per order (fee rounds up per order)")
    ap.add_argument("--fine", action="store_true", help="favourite buckets late in the window")
    a = ap.parse_args(argv)
    PB, LB = (FINE_PRICE, FINE_LEFT) if a.fine else (PRICE, LEFT)
    for coin in a.coins.split(","):
        ws, g, sp = run(coin, a.days, a.slip, a.qty, PB, LB)
        if not ws:
            print(f"\n{coin}: no cached markets")
            continue
        print(f"\n== {coin}: {len(ws)} markets, median spread {np.median(sp) * 100:.1f}c ==")
        print(f"  {'minutes left':>12} {'price':>9} {'n':>7} {'edge/contract after costs':>26} {'z':>6}")
        hits = 0
        for lb in LB:
            for pb in PB:
                v = np.array(g.get((lb, pb), []))
                if len(v) < 200:
                    continue
                # quotes within a market are correlated: count each market about once
                # per bucket by scaling the standard error by sqrt(minutes per market)
                per_mkt = max(len(v) / len(ws), 1.0)
                se = v.std(ddof=1) / math.sqrt(len(v) / per_mkt)
                z = v.mean() / se if se > 0 else 0.0
                mark = "  <-- EDGE" if v.mean() > 0 and z >= 3 else ""
                hits += bool(mark)
                print(f"  {lb[0] / 60:>4.0f}-{lb[1] / 60:<7.0f} {pb[0] * 100:4.1f}-{pb[1] * 100:4.1f}c {len(v):7d} "
                      f"{v.mean() * 100:+25.2f}c {z:+6.1f}{mark}")
        print(f"  {hits} bucket(s) with an edge after costs")


if __name__ == "__main__":
    main()
