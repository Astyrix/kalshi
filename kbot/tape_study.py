"""Study the real traders on Kalshi's KXBTC15M tape: who makes money, and how?

Every trade on Kalshi has a taker (paid the spread, crossed the book) and a
maker (rested a limit order). The tape is public: time to the microsecond,
price, size, and which side the taker bought. Matching every trade with how
its market settled shows where the money goes:

  taker P&L per contract = (1 if the side the taker bought won) - price paid
  maker P&L per contract = -(taker P&L)      (before fees)

broken down by trade size (retail clicks vs big bots), price, and minutes
left. It also measures mark-outs: how far the price moves in the taker's
favour 5 s / 30 s / 120 s after the trade. That is what a market maker loses
to informed flow (adverse selection), with far less noise than settlement.

  python -m kbot.tape_study --fetch 150      # download tapes of the latest 150 settled markets
  python -m kbot.tape_study                  # analyse what is cached
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from .data import KALSHI, MaxyClient
from .sweep import load_cached

TAPE = Path("data/cache/BTC-tape")


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def fetch_tape(client, ticker, max_pages=120):
    p = TAPE / f"{ticker}.npz"
    if p.exists():
        return np.load(p)
    rows, cursor = [], None
    for url in (f"{KALSHI}/markets/trades", f"{KALSHI}/historical/trades"):
        for _ in range(max_pages):
            q = {"ticker": ticker, "limit": 1000}
            if cursor:
                q["cursor"] = cursor
            d = client._get(url, q)
            tr = d.get("trades") or []
            for x in tr:
                rows.append((_t(x["created_time"]), float(x["yes_price_dollars"]),
                             float(x.get("count_fp") or 0), x.get("taker_side") == "yes",
                             bool(x.get("is_block_trade"))))
            cursor = d.get("cursor")
            if not cursor or not tr:
                break
        if rows:
            break
        cursor = None
    if not rows:
        return None
    a = np.array(sorted(rows), dtype=float)
    TAPE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(p, t=a[:, 0], yes=a[:, 1].astype(np.float32), n=a[:, 2].astype(np.float32),
                        taker_yes=a[:, 3].astype(bool), block=a[:, 4].astype(bool))
    return np.load(p)


def taker_fee_pc(p):
    return 0.07 * p * (1 - p)          # per contract, before Kalshi's round-up per order


SIZE = [(0, 10, "<10"), (10, 100, "10-99"), (100, 1000, "100-999"), (1000, 1e12, "1000+")]
LEFT = [(0, 60, "<1m"), (60, 180, "1-3m"), (180, 420, "3-7m"), (420, 901, "7-15m")]
PRICE = [(0, 0.10, "<10c"), (0.10, 0.30, "10-30c"), (0.30, 0.70, "30-70c"), (0.70, 0.90, "70-90c"), (0.90, 1.01, "90c+")]


def analyse(ws):
    by = {k: defaultdict(lambda: [0.0, 0.0, defaultdict(float)]) for k in ("size", "left", "price", "size_left")}
    marks = defaultdict(lambda: defaultdict(list))
    tot_n, tot_pnl, n_mk = 0.0, 0.0, 0
    for w in ws:
        p = TAPE / f"{w.ticker}.npz"
        if not p.exists() or w.result not in ("yes", "no"):
            continue
        d = np.load(p)
        t, yes, n, ty = d["t"], d["yes"].astype(float), d["n"].astype(float), d["taker_yes"]
        if len(t) < 50:
            continue
        n_mk += 1
        yes_won = w.result == "yes"
        px = np.where(ty, yes, 1 - yes)                       # price the taker paid for their side
        won = np.where(ty, yes_won, not yes_won).astype(float)
        pnl = won - px                                         # taker P&L per contract
        left = w.close_ts - t
        tot_n += n.sum()
        tot_pnl += (pnl * n).sum()
        for name, groups, val in (("size", SIZE, n), ("left", LEFT, left), ("price", PRICE, px)):
            for lo, hi, lab in groups:
                m = (val >= lo) & (val < hi)
                if m.any():
                    g = by[name][lab]
                    g[0] += n[m].sum()
                    g[1] += (pnl[m] * n[m]).sum()
                    g[2][w.ticker] += (pnl[m] * n[m]).sum()
        for slo, shi, slab in SIZE:
            for llo, lhi, llab in LEFT:
                m = (n >= slo) & (n < shi) & (left >= llo) & (left < lhi)
                if m.any():
                    g = by["size_left"][(slab, llab)]
                    g[0] += n[m].sum()
                    g[1] += (pnl[m] * n[m]).sum()
                    g[2][w.ticker] += (pnl[m] * n[m]).sum()
        # mark-outs: YES price of the last trade at or before t+h, signed toward the taker
        for h in (5, 30, 120):
            j = np.searchsorted(t, t + h, side="right") - 1
            ok = (t + h < w.close_ts - 1) & (j > np.arange(len(t)))
            move = (yes[j] - yes) * np.where(ty, 1, -1)
            for lo, hi, lab in SIZE:
                m = ok & (n >= lo) & (n < hi)
                if m.any():
                    marks[h][lab].append(((move[m] * n[m]).sum(), n[m].sum()))
    return n_mk, tot_n, tot_pnl, by, marks


def z_of(g):
    v = np.array(list(g[2].values()))
    if len(v) < 5 or v.std() == 0:
        return 0.0
    return v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", type=int, default=0, help="download tapes of the latest N settled markets")
    ap.add_argument("--days", type=float, default=12)
    a = ap.parse_args(argv)
    ws = [w for w in load_cached("BTC", a.days) if w.result in ("yes", "no")]
    if a.fetch:
        c = MaxyClient("BTC", min_interval=0.25)
        todo = ws[-a.fetch:]
        t0 = time.time()
        for i, w in enumerate(todo):
            try:
                fetch_tape(c, w.ticker)
            except Exception as e:
                print("skip", w.ticker, str(e)[:80], flush=True)
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(todo)} tapes [{time.time() - t0:.0f}s]", flush=True)
    n_mk, tot_n, tot_pnl, by, marks = analyse(ws)
    print(f"\n{n_mk} settled markets, {tot_n / 1e6:.1f}M contracts traded")
    print(f"takers overall: {tot_pnl / tot_n * 100:+.2f}c per contract before fees "
          f"(makers earn the opposite); taker fee ~{0.07 * 0.25 * 100:.2f}c at 50c\n")
    out = {"markets": n_mk, "contracts": tot_n, "taker_pnl_c": tot_pnl / tot_n * 100}
    for name, title in (("size", "trade size (contracts)"), ("left", "time left"), ("price", "price taker paid")):
        print(f"by {title}:   {'share of vol':>12} {'taker c/contract':>17} {'z':>6}")
        for lab, g in by[name].items():
            print(f"  {lab:10} {g[0] / tot_n:12.1%} {g[1] / g[0] * 100:+17.2f} {z_of(g):+6.1f}")
            out[f"{name}:{lab}"] = {"share": g[0] / tot_n, "taker_c": g[1] / g[0] * 100, "z": z_of(g)}
        print()
    print("taker c/contract by size x time left (z):")
    print("  " + " ".join(f"{l[2]:>14}" for l in LEFT))
    for _, _, s in SIZE:
        cells = []
        for _, _, l in LEFT:
            g = by["size_left"].get((s, l))
            cells.append(f"{g[1] / g[0] * 100:+6.2f} ({z_of(g):+4.1f})" if g and g[0] else " " * 14)
        print(f"  {s:8}" + " ".join(f"{c:>14}" for c in cells))
    print("\nmark-outs: how far price moves toward the taker after the trade (c), by size")
    for h, d in marks.items():
        print(f"  +{h:>3}s  " + "  ".join(f"{lab}: {sum(a for a, _ in v) / sum(b for _, b in v) * 100:+.2f}"
                                          for lab, v in d.items()))
        out[f"markout_{h}s"] = {lab: sum(a for a, _ in v) / sum(b for _, b in v) * 100 for lab, v in d.items()}
    Path("reports").mkdir(exist_ok=True)
    Path("reports/tape_study.json").write_text(json.dumps(out, indent=1, default=float))
    print("\nsaved reports/tape_study.json")


if __name__ == "__main__":
    main()
