"""Scalping study: trade the contract's price swings, not the settlement.

Uses the 1-minute Kalshi quotes in the cache. Two questions:

1. Ceiling: with perfect hindsight (buy at the lowest ask, sell at a later
   highest bid in the same window), how much do the swings pay after fees?
2. Rules: buy a side after its price drops sharply, sell at a profit target,
   after a time limit, or before the close. Parameters are chosen on the first
   half of the data and scored on the second half, so the result isn't fitted
   to the data it's scored on.

  python -m kbot.scalp --days 14
"""
from __future__ import annotations

import argparse
import itertools
from dataclasses import dataclass

from .strategy import taker_fee
from .sweep import load_cached

QTY = 10


def legs(w):
    """Per-minute (t, bid, ask) for each side, skipping one-sided quotes."""
    up, down = [], []
    for t, yb, ya, _ in w.quotes:
        if yb is None or ya is None or yb <= 0.005 or ya >= 0.995 or ya < yb or t >= w.close_ts:
            continue
        up.append((t, yb, ya))
        down.append((t, round(1 - ya, 4), round(1 - yb, 4)))
    return {"UP": up, "DOWN": down}


def pnl(entry, exit_, settled=False):
    fees = taker_fee(entry, QTY) + (0 if settled else taker_fee(exit_, QTY))
    return (exit_ - entry - fees) * QTY


def hindsight(windows, slip=0.01):
    """Best single round trip per window per side, perfectly timed."""
    total, n = 0.0, 0
    for w in windows:
        best = 0.0
        for side, q in legs(w).items():
            for i in range(len(q)):
                a = q[i][2] + slip
                for j in range(i + 1, len(q)):
                    best = max(best, pnl(a, q[j][1] - slip))
        total += best
        n += best > 0
    return total, n


@dataclass(frozen=True)
class Rule:
    drop: float      # buy after the ask fell this much over `lookback` minutes
                     # (negative = buy after it ROSE this much: momentum)
    lookback: int
    lo: float        # only buy contracts priced in [lo, hi]
    hi: float
    target: float    # sell once bid >= entry + target
    hold: int        # or after this many minutes
    stop: float      # or once bid <= entry - stop


def run_rule(windows, r: Rule, slip=0.01):
    trades = []
    for w in windows:
        for side, q in legs(w).items():
            i = r.lookback
            while i < len(q):
                t, bid, ask = q[i]
                prev_ask = q[i - r.lookback][2]
                if r.lo <= ask <= r.hi and ((r.drop > 0 and prev_ask - ask >= r.drop) or (r.drop < 0 and ask - prev_ask >= -r.drop)) and w.close_ts - t > 90:
                    entry = ask + slip
                    out = None
                    for j in range(i + 1, min(i + 1 + r.hold, len(q))):
                        b = q[j][1] - slip
                        if b >= entry + r.target or b <= entry - r.stop:
                            out = (j, b)
                            break
                    if out is None:                       # time limit: sell at bid
                        j = min(i + r.hold, len(q) - 1)
                        out = (j, q[j][1] - slip) if j > i else None
                    if out is None:                       # no later quote: settles
                        won = (side == "UP") == (w.result == "yes")
                        trades.append(pnl(entry, 1.0 if won else 0.0, settled=True))
                        break
                    trades.append(pnl(entry, max(out[1], 0.0)))
                    i = out[0] + 1
                    continue
                i += 1
    return trades


def stats(tr):
    if not tr:
        return "  0 trades"
    wins = sum(x > 0 for x in tr)
    return f"{len(tr):5d} trades  win {wins / len(tr):5.1%}  P&L ${sum(tr):8.2f}  avg ${sum(tr) / len(tr):+.3f}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", default="BTC")
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--slippage", type=float, default=0.01)
    a = ap.parse_args(argv)
    ws = load_cached(a.coin, a.days)
    half = len(ws) // 2
    train, test = ws[:half], ws[half:]
    print(f"{len(ws)} windows ({len(train)} to pick settings, {len(test)} to test)\n")

    tot, n = hindsight(ws, a.slippage)
    print(f"CEILING (perfect hindsight, 1 round trip per window, {QTY} contracts):")
    print(f"  ${tot:.2f} total, profitable swing in {n}/{len(ws)} windows "
          f"(${tot / len(ws):.2f} per window)\n")

    grid = [Rule(d, lb, lo, hi, tg, h, st)
            for d, lb, (lo, hi), tg, h, st in itertools.product(
                [0.05, 0.10, 0.15, 0.25, -0.05, -0.10, -0.15], [1, 2],
                [(0.02, 0.10), (0.10, 0.35), (0.35, 0.65), (0.02, 0.50)],
                [0.03, 0.06, 0.10], [1, 2, 4], [0.05, 0.10, 1.0])]
    scored = sorted(((sum(run_rule(train, r, a.slippage)), r) for r in grid),
                    key=lambda x: x[0], reverse=True)
    print(f"Tried {len(grid)} rule settings on the first half. Best 3 overall + best 3 momentum, scored on the unseen second half:")
    best_m = [x for x in scored if x[1].drop < 0][:3]
    for p, r in scored[:3] + best_m:
        print(f"  drop>={r.drop:.2f}/{r.lookback}m price {r.lo:.2f}-{r.hi:.2f} target +{r.target:.2f} "
              f"hold {r.hold}m stop {r.stop:.2f}")
        print(f"     train {stats(run_rule(train, r, a.slippage))}")
        print(f"     TEST  {stats(run_rule(test, r, a.slippage))}")
    pos = sum(1 for p, _ in scored if p > 0)
    print(f"\n{pos}/{len(grid)} settings were profitable on the first half.")


if __name__ == "__main__":
    main()
