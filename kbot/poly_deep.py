"""What do the winning Polymarket wallets do that the losers don't?

For every BUY fill in Polymarket's BTC 15-minute windows (timestamps shifted
3 s earlier: the tape is ~3 s late versus Kalshi), compare the price paid
with:

  fair     a fair value from live spot: P(up) = Phi(ln(S_t/S_open) / (sigma sqrt(T-t))),
           S = OKX BTC-USDT 1 s closes, sigma = 1 s volatility over the
           previous 5 minutes. Edge vs fair = fair(side) - price.
  markout  fair(side) 30 s and 120 s later minus fair now: does the wallet
           buy just before spot moves its way (speed / momentum)?
  kalshi   Kalshi's price for the same side in the same second (where the
           Kalshi tape is cached): does it buy on Polymarket when Kalshi,
           the 60x bigger venue, already prices that side higher?
  result   won - price paid (before fees; Polymarket's taker fee is
           0.05 * p * (1 - p) per share).

Groups: wallets with z >= 2.5 over >= 100 markets (winners), z <= -1.5
(losers), and every fill (everyone).

  python -m kbot.poly_deep --days 7
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests

from .latency_study import spot_1s
from .poly_study import CACHE, describe, wallet_stats
from .sweep import load_cached
from .tape_study import TAPE

SHIFT = -3


def Phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--min-markets", type=int, default=100)
    a = ap.parse_args(argv)
    files = sorted(CACHE.glob("btc-updown-15m-*.json.gz"))
    pws = [json.load(gzip.open(f, "rt")) for f in files]
    pws = [w for w in pws if w["end"] >= max(x["end"] for x in pws) - a.days * 86400]
    W = wallet_stats(pws)
    st = {w: describe(s) for w, s in W.items()}
    reg = {w: r for w, r in st.items() if r["markets"] >= a.min_markets}
    winners = {w for w, r in reg.items() if r["z"] >= 2.5}
    losers = {w for w, r in reg.items() if r["z"] <= -1.5}
    print(f"{len(pws)} windows, {len(reg)} regular wallets: {len(winners)} winners (z>=2.5), {len(losers)} losers (z<=-1.5)",
          flush=True)
    kal = {w.close_ts: w for w in load_cached("BTC", a.days + 1)}
    sess = requests.Session()
    G = defaultdict(lambda: defaultdict(list))         # group -> metric -> list of (value, size)
    per_w = defaultdict(lambda: defaultdict(list))
    for k, pw in enumerate(pws):
        start, end = pw["start"], pw["end"]
        try:
            sp = spot_1s(start - 300, end, sess)
        except Exception:
            continue
        if len(sp) < 900:
            continue
        secs = np.arange(start - 300, end + 1)
        keys = np.array(sorted(sp))
        vals = np.array([sp[x] for x in keys])
        j = np.clip(np.searchsorted(keys, secs, side="right") - 1, 0, None)
        S = vals[j]
        lr = np.diff(np.log(S), prepend=np.log(S[0]))
        s0 = S[300]                                   # spot at the window open

        def fair_up(t):
            i = int(t) - (start - 300)
            if i < 300 or i >= len(S):
                return None
            sig = lr[i - 300:i].std() or 1e-5
            left = max(end - t, 1)
            return Phi(math.log(S[i] / s0) / (sig * math.sqrt(left)))
        kw = kal.get(end)
        tape = None
        if kw is not None and (TAPE / f"{kw.ticker}.npz").exists():
            d = np.load(TAPE / f"{kw.ticker}.npz")
            tape = (d["t"], d["yes"].astype(float))
        for ts, wal, side, oi, px, sz, _ in pw["trades"]:
            if side != "BUY" or not (0.03 <= px <= 0.97):
                continue
            t = ts + SHIFT
            if t > end - 15:
                continue
            f0 = fair_up(t)
            if f0 is None:
                continue
            up = oi == 0
            fs = lambda f: f if up else 1 - f
            won = 1.0 if oi == pw["winner"] else 0.0
            m = {"edge_vs_fair": fs(f0) - px, "result": won - px,
                 "net_result": won - px - 0.05 * px * (1 - px), "price": px,
                 "secs_left": end - t,
                 "with_30s_move": 1.0 if (S[int(t) - start + 300] - S[int(t) - start + 270]) * (1 if up else -1) > 0 else 0.0}
            for h in (30, 120):
                f1 = fair_up(t + h) if t + h < end - 5 else None
                if f1 is not None:
                    m[f"markout_{h}s"] = fs(f1) - fs(f0)
            if tape is not None:
                tt, yy = tape
                lo, hi = np.searchsorted(tt, [t, t + 1])
                if hi > lo:
                    ky = float(np.median(yy[lo:hi]))
                    m["kalshi_minus_price"] = (ky if up else 1 - ky) - px
            grp = ["everyone"] + (["winners"] if wal in winners else []) + (["losers"] if wal in losers else [])
            for g in grp:
                for key, v in m.items():
                    G[g][key].append((v, sz))
            if wal in winners:
                for key, v in m.items():
                    per_w[wal][key].append((v, sz))
        if (k + 1) % 100 == 0:
            print(f"  {k + 1}/{len(pws)} windows", flush=True)

    def avg(v):
        v = np.array(v)
        return float(np.average(v[:, 0], weights=v[:, 1])) if len(v) else float("nan")
    keys = ["price", "secs_left", "edge_vs_fair", "markout_30s", "markout_120s", "kalshi_minus_price",
            "with_30s_move", "result", "net_result"]
    print("\nsize-weighted averages (cents unless noted):")
    print(f"{'group':10} " + " ".join(f"{k[:14]:>14}" for k in keys))
    out = {}
    for g in ("winners", "everyone", "losers"):
        r = {k: avg(G[g][k]) for k in keys}
        out[g] = r
        cells = [f"{r[k] * (1 if k in ('secs_left',) else 100):14.2f}" if k != "with_30s_move" else f"{r[k]:14.1%}"
                 for k in keys]
        print(f"{g:10} " + " ".join(cells))
    print("\nper winning wallet:")
    for w in sorted(per_w, key=lambda w: -reg[w]["pnl"])[:12]:
        r = {k: avg(per_w[w][k]) for k in keys}
        print(f"  {w[:12]} pnl ${reg[w]['pnl']:>9,.0f} mkts {reg[w]['markets']:4d} both-sides {reg[w]['both_sides_share']:.0%} | "
              f"px {r['price'] * 100:4.0f}c left {r['secs_left']:4.0f}s edge-vs-fair {r['edge_vs_fair'] * 100:+5.1f}c "
              f"mk30 {r['markout_30s'] * 100:+5.1f}c mk120 {r['markout_120s'] * 100:+5.1f}c "
              f"kalshi-px {r['kalshi_minus_price'] * 100:+5.1f}c with-move {r['with_30s_move']:.0%} "
              f"net {r['net_result'] * 100:+5.1f}c")
        out[w] = r
    # does the fair-value model itself find value? (all fills, bucketed by edge vs fair)
    e = np.array(G["everyone"]["edge_vs_fair"])
    res = np.array(G["everyone"]["net_result"])
    print("\nall taker buys bucketed by edge vs fair value (is the simple spot model right where the price disagrees?):")
    for lo, hi in ((-1, -0.10), (-0.10, -0.05), (-0.05, -0.02), (-0.02, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 1)):
        mk = (e[:, 0] >= lo) & (e[:, 0] < hi)
        if mk.sum() > 50:
            print(f"  fair - price in [{lo * 100:+4.0f}, {hi * 100:+4.0f})c: n {mk.sum():6d}, "
                  f"net result {np.average(res[mk, 0], weights=res[mk, 1]) * 100:+6.2f}c per share")
    Path("reports/poly_deep.json").write_text(json.dumps(out, indent=1))
    print("\nsaved reports/poly_deep.json")


if __name__ == "__main__":
    main()
