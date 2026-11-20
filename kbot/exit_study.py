"""Which exit works best on cheap late entries, at 5-second resolution?

Uses the top-of-book snapshots the paper trader records every 5 seconds
(data/ticks/<ticker>.jsonl.gz). Every snapshot where a side's ask is at or
under --max-price with 1.5-4 minutes left is a hypothetical entry (bought at
ask + 1c). Each exit rule is replayed on the bids that followed:

  minute_tp     the backtest's rule: +TP / -SL checked once a minute
  fast_tp       +TP / -SL checked every 5 seconds
  trail         after +TP, sell once the bid gives back TP from its peak
  trail_wide    same, giving back 2x TP
  hold          hold to settlement
  fast_tp_hold  -SL checked every 5 s, otherwise hold to settlement

  python -m kbot.exit_study --tp 0.05 --sl 0.05
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from .data import MaxyClient, _ts
from .strategy import taker_fee

SLIP, QTY = 0.01, 10


def load_ticks(p: Path):
    rows = []
    with gzip.open(p, "rt") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return [r for r in rows if r[1] is not None and r[2] is not None]


def pnl(entry, px):
    return (px - entry - taker_fee(min(entry, 0.99), QTY) - taker_fee(max(px, 0.01), QTY)) * QTY


def settle(entry, won):
    return ((1.0 if won else 0.0) - entry - taker_fee(min(entry, 0.99), QTY)) * QTY


def simulate(path, entry, won, tp, sl, t_entry):
    """path: [(ts, bid)] after entry, last one about a minute before close."""
    out = {}
    # minute_tp: only look at the bid at whole minutes after entry
    out["minute_tp"] = None
    for t, b in path:
        if (t - t_entry) % 60 < 5 and t - t_entry >= 55:
            if b >= entry + tp or b <= entry - sl:
                out["minute_tp"] = pnl(entry, b)
                break
    if out["minute_tp"] is None:
        out["minute_tp"] = pnl(entry, path[-1][1]) if path[-1][1] > 0.01 else settle(entry, won)
    out["fast_tp"] = next((pnl(entry, b) for _, b in path if b >= entry + tp or b <= entry - sl),
                          pnl(entry, path[-1][1]) if path[-1][1] > 0.01 else settle(entry, won))
    for name, give in (("trail", tp), ("trail_wide", 2 * tp)):
        peak, res = None, None
        for _, b in path:
            if peak is None:
                if b <= entry - sl:
                    res = pnl(entry, b)
                    break
                if b >= entry + tp:
                    peak = b
                continue
            peak = max(peak, b)
            if b <= peak - give:
                res = pnl(entry, b)
                break
        if res is None:
            last = path[-1][1]
            res = settle(entry, won) if (peak is not None and last >= 0.9) or last <= 0.01 else pnl(entry, last)
        out[name] = res
    out["hold"] = settle(entry, won)
    out["fast_tp_hold"] = next((pnl(entry, b) for _, b in path if b <= entry - sl), settle(entry, won))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tp", type=float, default=0.05)
    ap.add_argument("--sl", type=float, default=0.05)
    ap.add_argument("--max-price", type=float, default=0.25)
    a = ap.parse_args(argv)
    client = MaxyClient("BTC")
    files = sorted(Path("data/ticks").glob("*.jsonl.gz"))
    res = defaultdict(list)
    n_mk = 0
    for f in files:
        ticker = f.name.replace(".jsonl.gz", "")
        m = client.market(ticker)
        if not m or m.get("result") not in ("yes", "no"):
            continue                                   # not settled yet
        close = _ts(m["close_time"])
        ticks = load_ticks(f)
        if not ticks or ticks[-1][0] < close - 90:
            continue                                   # recording didn't cover the end
        n_mk += 1
        last_entry = {"up": -10 ** 9, "dn": -10 ** 9}
        for i, (t, yb, ya, *_) in enumerate(ticks):
            left = close - t
            if not 90 <= left <= 240:
                continue
            for side in ("up", "dn"):
                ask = ya if side == "up" else round(1 - yb, 4)
                if ask > a.max_price or ask < 0.01 or t - last_entry[side] < 30:
                    continue
                entry = ask + SLIP
                path = [(tt, (b if side == "up" else round(1 - aa, 4)) - SLIP)
                        for tt, b, aa, *_ in ticks[i + 1:] if tt <= close - 60]
                if not path:
                    continue
                won = (side == "up") == (m["result"] == "yes")
                for k, v in simulate(path, entry, won, a.tp, a.sl, t).items():
                    res[k].append(v)
                last_entry[side] = t
    n = len(res.get("hold", []))
    print(f"{n_mk} settled markets with 5-second data, {n} cheap late entries "
          f"(<= {a.max_price * 100:.0f}c, 1.5-4 min left), TP {a.tp} SL {a.sl}, 10 contracts\n")
    if not n:
        print("not enough tick data yet: leave `python -m kbot paper` running")
        return
    for k, v in sorted(res.items(), key=lambda kv: -sum(kv[1])):
        v = np.array(v)
        print(f"  {k:14} total ${v.sum():8.2f}  avg ${v.mean():+.3f}  win {np.mean(v > 0):5.1%}")
    print("\nThese entries are NOT model-filtered (every cheap late moment), so compare exits "
          "with each other, not with the model's P&L.")


if __name__ == "__main__":
    main()
