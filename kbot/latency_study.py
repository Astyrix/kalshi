"""Latency arbitrage check: does Kalshi's price lag spot by seconds?

The strategy that made the most money for bots in 15-minute crypto markets
(Polymarket added taker fees in Jan 2026 to stop it) is latency arbitrage:
BTC jumps on a big exchange, the prediction market's resting quotes are
stale for a second or two, and a fast bot takes them before they move.

Using the Kalshi trade tape (microsecond timestamps, kbot.tape_study) and
OKX 1-second BTC candles, this measures:

  lead/lag     correlation of 1 s Kalshi YES price changes with 1 s spot
               changes L seconds earlier (peak at L > 0 = Kalshi lags)
  jump events  after a sharp 2 s spot move, what do takers pay for the side
               the move favours, d seconds later, and how does it settle?
               If d = 1-2 s still pays off after fees, there is a window a
               fast bot could use; if the edge is gone by d = 0-1 s, only
               co-located HFT can play.

  python -m kbot.latency_study
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import requests

from .sweep import load_cached
from .tape_study import TAPE

OKX = "https://www.okx.com/api/v5/market/history-candles"
SPOT = Path("data/cache/OKX-1s")


def spot_1s(start, end, s=None):
    """{second: close} for [start, end) from OKX BTC-USDT 1 s candles (cached per market)."""
    p = SPOT / f"{start}.json"
    if p.exists():
        return {int(k): v for k, v in json.loads(p.read_text()).items()}
    s = s or requests.Session()
    out, after = {}, (end + 1) * 1000
    for _ in range(20):
        d = s.get(OKX, params={"instId": "BTC-USDT", "bar": "1s", "limit": 100, "after": after}, timeout=15).json()
        rows = d.get("data") or []
        if not rows:
            break
        for r in rows:
            t = int(r[0]) // 1000
            if start - 30 <= t <= end:
                out[t] = float(r[4])
        after = int(rows[-1][0])
        if after // 1000 < start - 30:
            break
    SPOT.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out))
    return out


def ffill(keys, vals, grid):
    j = np.searchsorted(keys, grid, side="right") - 1
    out = np.where(j >= 0, vals[np.clip(j, 0, None)], np.nan)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--jump-q", type=float, default=0.995, help="quantile of |2 s spot move| that counts as a jump")
    a = ap.parse_args(argv)
    ws = [w for w in load_cached("BTC", a.days) if w.result in ("yes", "no") and (TAPE / f"{w.ticker}.npz").exists()]
    print(f"{len(ws)} markets with a tape", flush=True)
    sess = requests.Session()
    LAGS = list(range(-5, 11))
    xc = {L: [0.0, 0.0, 0.0, 0] for L in LAGS}        # sum xy, sum xx, sum yy, n
    mkts = []
    for w in ws:
        try:
            sp = spot_1s(w.open_ts, w.close_ts, sess)
        except Exception as e:
            print("spot fail", w.ticker, str(e)[:60])
            continue
        if len(sp) < 600:
            continue
        d = np.load(TAPE / f"{w.ticker}.npz")
        t, yes, ty, n = d["t"], d["yes"].astype(float), d["taker_yes"], d["n"].astype(float)
        grid = np.arange(w.open_ts, w.close_ts - 1)
        sk = np.array(sorted(sp))
        S = ffill(sk, np.array([sp[k] for k in sk]), grid + 0.999)     # spot close of each second
        K = ffill(t, yes, grid + 0.999)                                # last Kalshi YES print in each second
        dS, dK = np.diff(S), np.diff(K)
        live = (grid[1:] < w.close_ts - 60)
        for L in LAGS:
            if L >= 0:
                x, y = dS[:len(dS) - L], dK[L:]
                m = live[L:]
            else:
                x, y = dS[-L:], dK[:L]
                m = live[:L]
            ok = m & np.isfinite(x) & np.isfinite(y)
            x, y = x[ok] - 0, y[ok]
            xc[L][0] += float((x * y).sum())
            xc[L][1] += float((x * x).sum())
            xc[L][2] += float((y * y).sum())
            xc[L][3] += int(ok.sum())
        mkts.append((w, grid, S, t, yes, ty, n))
    print("\nlead/lag: corr(Kalshi YES change at t+L, spot change at t), 1 s steps")
    for L in LAGS:
        sxy, sxx, syy, k = xc[L]
        c = sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0
        print(f"  L={L:+3d}s  corr {c:+.3f}  {'#' * int(max(c, 0) * 100)}")
    # jumps
    moves = np.concatenate([np.abs(S[2:] - S[:-2]) for _, _, S, *_ in mkts])
    thr = float(np.nanquantile(moves, a.jump_q))
    print(f"\njump = |2 s spot move| >= ${thr:.1f} (top {100 * (1 - a.jump_q):.1f}% of seconds)")
    DEL = [0, 1, 2, 3, 5, 10, 30]
    res = {dd: [] for dd in DEL}
    n_ev = 0
    for w, grid, S, t, yes, ty, n in mkts:
        mv = S[2:] - S[:-2]
        last = -99
        for i in np.where(np.abs(mv) >= thr)[0]:
            te = grid[i + 2] + 1                        # end of the 2 s move
            if te - last < 10 or te > w.close_ts - 90:
                continue
            last = te
            n_ev += 1
            up = mv[i] > 0
            won = (w.result == "yes") == up
            for dd in DEL:
                lo, hi = np.searchsorted(t, [te + dd, te + dd + 1])
                sel = np.arange(lo, hi)
                sel = sel[ty[sel] == up]               # takers buying the side the jump favours
                if len(sel) == 0:
                    continue
                px = float(np.average(np.where(up, yes[sel], 1 - yes[sel]), weights=n[sel]))
                if not (0.05 <= px <= 0.95):
                    continue
                fee = 0.07 * px * (1 - px)
                res[dd].append((1.0 if won else 0.0) - px - fee)
    print(f"{n_ev} jumps; buying the favoured side at the price takers paid d seconds after the jump,")
    print("held to settlement, minus the taker fee (per contract):")
    out = {"threshold_usd": thr, "events": n_ev, "lag_corr": {}, "jump": {}}
    for L in LAGS:
        sxy, sxx, syy, _ = xc[L]
        out["lag_corr"][L] = sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0
    for dd in DEL:
        v = np.array(res[dd])
        if len(v) < 10:
            continue
        z = v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))
        print(f"  d={dd:>2}s  n={len(v):5d}  {v.mean() * 100:+6.2f}c  z {z:+5.1f}")
        out["jump"][dd] = {"n": len(v), "pnl_c": v.mean() * 100, "z": z}
    Path("reports").mkdir(exist_ok=True)
    Path("reports/latency_study.json").write_text(json.dumps(out, indent=1))
    print("\nsaved reports/latency_study.json")


if __name__ == "__main__":
    main()
