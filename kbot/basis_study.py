"""Basis risk between Kalshi and Polymarket's 15-minute BTC windows.

Kalshi settles on CF BRTI (strike = BRTI at the open, result = BRTI at the
close); Polymarket on Chainlink BTC/USD (priceToBeat at the open, finalPrice
at the close). A cross-venue pair (Polymarket Up + Kalshi NO, or Polymarket
Down + Kalshi YES) pays $1 when both settle the same way, but $0 or $2 when
they split. They split when BTC ends closer to the strike than the change
in the Chainlink-BRTI gap over the window:

    offset = Chainlink - BRTI;   delta = offset(close) - offset(open)
    Polymarket Up + Kalshi NO pays 0 iff 0 < BRTI move < -delta   (delta < 0)
    Polymarket Down + Kalshi YES pays 0 iff -delta < BRTI move < 0 (delta > 0)

This measures, over every window both venues have settled:
  * how often they split, and how big |delta| gets
  * whether delta is predictable (mean reversion of the offset, BTC's move)
  * --hist: every historical moment a Polymarket taker bought one side and a
    Kalshi taker bought the other within the same second (both tapes, with
    the 3 s clock fix), and what different entry rules would have made.
    Windows where the venues split are over-represented among the cached
    tapes, so results are reweighted to the true split rate.

  python -m kbot.basis_study
  python -m kbot.basis_study --hist
"""
from __future__ import annotations

import argparse
import gzip
import json
import time
from pathlib import Path

import numpy as np
import requests

from .poly_study import CACHE, GAMMA
from .sweep import load_cached
from .tape_study import TAPE
from .xvenue_watch import brti_vol, final_moves, near_strike

META = Path("data/cache/POLY-BTC15-meta.json")


def poly_meta(starts, s=None):
    """{start: [priceToBeat, finalPrice]} from Gamma's eventMetadata (cached)."""
    try:
        meta = {int(k): v for k, v in json.loads(META.read_text()).items()}
    except Exception:
        meta = {}
    s = s or requests.Session()
    todo = [st for st in starts if st not in meta]
    for i, st in enumerate(todo):
        for k in range(4):
            try:
                time.sleep(0.12)
                e = s.get(f"{GAMMA}/events", params={"slug": f"btc-updown-15m-{st}"}, timeout=15).json()
                break
            except Exception:
                time.sleep(2 * 2 ** k)
        else:
            continue
        md = (e[0].get("eventMetadata") or {}) if e else {}
        if md.get("priceToBeat") and md.get("finalPrice"):
            meta[st] = [float(md["priceToBeat"]), float(md["finalPrice"])]
        if (i + 1) % 200 == 0:
            print(f"  meta {i + 1}/{len(todo)}", flush=True)
            META.write_text(json.dumps(meta))
    META.parent.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps(meta))
    return meta


def windows(days=15):
    """Every window settled on both venues, with both strikes and both settlement prices."""
    kal = {w.close_ts: w for w in load_cached("BTC", days) if w.result in ("yes", "no") and w.strike}
    starts = [c - 900 for c in kal if (CACHE / f"btc-updown-15m-{c - 900}.json.gz").exists()]
    meta = poly_meta(starts)
    out = []
    for st in sorted(starts):
        if st not in meta:
            continue
        w = kal[st + 900]
        with gzip.open(CACHE / f"btc-updown-15m-{st}.json.gz", "rt") as fh:
            winner = json.load(fh)["winner"]
        pb, pf = meta[st]
        out.append({"start": st, "ticker": w.ticker, "k_strike": w.strike, "k_final": w.settle_value,
                    "k_yes": w.result == "yes", "p_strike": pb, "p_final": pf, "p_up": winner == 0,
                    "w": w})
    return out


def main(argv=None):
    W = windows()
    if not W:
        raise SystemExit("no windows")
    off0 = np.array([x["p_strike"] - x["k_strike"] for x in W])
    off1 = np.array([x["p_final"] - x["k_final"] for x in W])
    delta = off1 - off0
    move = np.array([x["k_final"] - x["k_strike"] for x in W])
    split = np.array([x["k_yes"] != x["p_up"] for x in W])
    print(f"{len(W)} windows settled on both venues; split {split.sum()} ({split.mean():.2%})")
    print(f"offset Chainlink - BRTI at open: median ${np.median(off0):+.2f}, 5-95% "
          f"[{np.quantile(off0, .05):+.1f}, {np.quantile(off0, .95):+.1f}]")
    a = np.abs(delta)
    print(f"|delta offset| over a window: median ${np.median(a):.2f}, 90% ${np.quantile(a, .9):.1f}, "
          f"99% ${np.quantile(a, .99):.1f}, max ${a.max():.1f}")
    # the rule that predicts a split from the two strikes and settlement prices
    pred = ((move > 0) & (move < -delta)) | ((move < 0) & (move > -delta))
    print(f"split explained by |BRTI move| < |delta|: {(pred == split).mean():.1%} agreement")
    print("\nsplit rate by |BRTI move from Kalshi strike at close|:")
    am = np.abs(move)
    for lo, hi in ((0, 5), (5, 10), (10, 20), (20, 40), (40, 80), (80, 1e9)):
        m = (am >= lo) & (am < hi)
        if m.any():
            print(f"  ${lo:>3}-{hi if hi < 1e9 else 'inf':>4}: {m.sum():5d} windows, split {split[m].mean():6.1%}")
    # is delta predictable at the open?
    print("\nis the change in offset predictable?")
    print(f"  corr(delta, offset at open)        {np.corrcoef(delta, off0)[0, 1]:+.3f}   (negative = offset mean-reverts)")
    print(f"  corr(delta, BRTI move this window) {np.corrcoef(delta, move)[0, 1]:+.3f}")
    prev = np.r_[np.nan, delta[:-1]]
    ok = np.isfinite(prev)
    print(f"  corr(delta, previous window delta) {np.corrcoef(delta[ok], prev[ok])[0, 1]:+.3f}")
    med = np.median(off0)
    b = np.polyfit(off0 - med, delta, 1)
    resid = delta - np.polyval(b, off0 - med)
    print(f"  delta = {b[0]:+.3f} * (offset - median) {b[1]:+.2f}; residual sd ${resid.std():.2f} vs raw sd ${delta.std():.2f}")

    return W, b, med, resid


def split_risk(dist, sig, left, D):
    """(P(Poly Up + Kalshi NO pays 0), P(Poly Down + Kalshi YES pays 0)) given BRTI - strike
    now, volatility, time left and samples D of the change in Chainlink - BRTI by the close."""
    m = np.sort(dist + final_moves(sig, left))
    n = len(m)
    F = lambda x: np.searchsorted(m, x) / n
    lo = F(0.0)
    hi = F(-D)
    up_adv = np.where(D < 0, hi - lo, 0).mean()       # 0 < m < -D
    dn_adv = np.where(D > 0, lo - hi, 0).mean()       # -D < m < 0
    return float(up_adv), float(dn_adv)


def hist(W, b, med, resid, shift=-3):
    """Matched historical pairs: a Polymarket taker BUY and Kalshi takers buying the other side
    within the same second; returns one dict per match with the entry-time features."""
    E = []
    for x in W:
        tk, w = x["ticker"], x["w"]
        if not (TAPE / f"{tk}.npz").exists():
            continue
        idx, close = np.asarray(w.index, dtype=float), w.close_ts
        with gzip.open(CACHE / f"btc-updown-15m-{x['start']}.json.gz", "rt") as fh:
            pw = json.load(fh)
        d = np.load(TAPE / f"{tk}.npz")
        t, yes, ty = d["t"], d["yes"].astype(float), d["taker_yes"]
        D = b[0] * (x["p_strike"] - x["k_strike"] - med) + b[1] + resid
        memo = {}
        for ts, wal, side, oi, px, sz, _ in pw["trades"]:
            tt = ts + shift
            if side != "BUY" or not (0.01 <= px <= 0.99) or tt > close - 3:
                continue
            lo_, hi_ = np.searchsorted(t, [tt, tt + 1])
            sel = np.arange(lo_, hi_)
            want_yes = oi == 1                       # Poly Down -> Kalshi YES; Poly Up -> Kalshi NO
            sel = sel[ty[sel] == want_yes]
            if not len(sel):
                continue
            q = float(np.median(np.where(want_yes, yes[sel], 1 - yes[sel])))
            if tt not in memo:
                j = np.searchsorted(idx[:, 0], tt, side="right") - 1
                if j < 120:
                    memo[tt] = None
                    continue
                dist, sig, left = idx[j, 1] - w.strike, brti_vol(idx[:j + 1, 1]), close - tt
                memo[tt] = (dist, sig, left, near_strike(dist, sig, left), split_risk(dist, sig, left, D))
            if memo[tt] is None:
                continue
            dist, sig, left, near, (ua, da) = memo[tt]
            up = oi == 0
            # market-implied change in Chainlink - BRTI: the distance each venue's price implies
            mv = final_moves(sig, left)
            pU = min(max(px if up else 1 - px, 0.01), 0.99)
            kY = min(max(1 - q if up else q, 0.01), 0.99)
            d_imp = float(np.quantile(mv, 1 - kY) - np.quantile(mv, 1 - pU))
            pa, pd_ = split_risk(dist, sig, left, d_imp + 0.5 * resid)
            E.append(dict(ts=tt, tk=tk, up=up, size=min(sz, 50.0), left=left, dist=dist, near=near,
                          net=1 - px - q - 0.05 * px * (1 - px) - 0.07 * q * (1 - q),
                          risk=ua + da, post=max(pa + pd_, ua + da),
                          pay=float(oi == pw["winner"]) + float(want_yes == (w.result == "yes")),
                          split=x["k_yes"] != x["p_up"]))
    E.sort(key=lambda e: e["ts"])
    return E


def simulate(E, rule, thr, per_entry=50, per_window=500, gap=5, delay=0, limit=True):
    """Enter on matched pairs where rule(e) >= thr. delay > 0: the order arrives `delay` s
    later and fills against the next matched pair in that direction 'delay' to 'delay'+2 s
    after the signal, at its prices; with limit=True only if that pair's edge is still >= 0."""
    import bisect
    from collections import defaultdict
    by = defaultdict(list)
    for e in E:
        by[(e["tk"], e["up"])].append(e)
    tsl = {k: [e["ts"] for e in v] for k, v in by.items()}
    used, last, out = {}, {}, []
    for e in E:
        if rule(e) < thr:
            continue
        key = (e["tk"], e["up"])
        if e["ts"] - last.get(key, -1e9) < gap:
            continue
        f = e
        if delay:
            i = bisect.bisect_left(tsl[key], e["ts"] + delay)
            if i >= len(tsl[key]) or tsl[key][i] > e["ts"] + delay + 2:
                continue
            f = by[key][i]
            if limit and f["net"] < 0:
                continue
        q = min(f["size"], per_entry, per_window - used.get(e["tk"], 0))
        if q < 1:
            continue
        out.append((e["tk"], q, q * (f["pay"] - (1 - f["net"])), e["split"]))
        used[e["tk"]] = used.get(e["tk"], 0) + q
        last[key] = e["ts"]
    return out


RULES = {
    "edge after fees": lambda e: e["net"],
    "edge - P(split)": lambda e: e["net"] - e["risk"],
    "edge - P(split | price-implied drift)": lambda e: e["net"] - e["post"],
    "edge, if P(end within $15) < 1%": lambda e: e["net"] if e["near"] < 0.01 else -1,
    "edge, if P(end within $15) < 3%": lambda e: e["net"] if e["near"] < 0.03 else -1,
    "edge, if P(end within $15) < 10%": lambda e: e["net"] if e["near"] < 0.10 else -1,
}


def evaluate(E, rate):
    wins = {e["tk"] for e in E}
    n_split = len({e["tk"] for e in E if e["split"]})
    n_norm = len(wins) - n_split
    print(f"\n{len(E)} matched pairs in {len(wins)} windows ({n_split} where the venues split); "
          f"split windows reweighted to the true rate {rate:.2%}")
    print(f"{'rule':40} {'thr':>4} {'fill':>18} {'pairs':>6} {'normal $/win':>12} {'split $/win':>11} {'EV $/win':>9} "
          f"{'c/pair':>7} {'break-even split rate':>21}")
    out = {}
    for name, f in RULES.items():
        for thr, delay, limit in ((0.005, 0, True), (0.01, 0, True), (0.02, 0, True),
                                  (0.005, 2, True), (0.005, 2, False)):
            t = simulate(E, f, thr, delay=delay, limit=limit)
            fill = "at signal" if not delay else f"{delay} s late, " + ("limit" if limit else "any price")
            if not t:
                continue
            ok = [x for x in t if not x[3]]
            bad = [x for x in t if x[3]]
            gn = sum(x[2] for x in ok) / max(n_norm, 1)
            gs = sum(x[2] for x in bad) / max(n_split, 1)
            pn = sum(x[1] for x in ok) / max(n_norm, 1)
            ps = sum(x[1] for x in bad) / max(n_split, 1)
            ev = (1 - rate) * gn + rate * gs
            evc = ev / ((1 - rate) * pn + rate * ps) * 100 if pn else 0
            be = gn / (gn - gs) if gs < 0 < gn else (1.0 if gs >= 0 else 0.0)
            print(f"{name:40} {thr * 100:4.1f} {fill:>18} {sum(x[1] for x in t):6.0f} {gn:+12.2f} {gs:+11.2f} {ev:+9.2f} "
                  f"{evc:+7.2f} {be:21.1%}")
            out[f"{name} @ {thr} / {fill}"] = {"normal_per_window": gn, "split_per_window": gs, "ev_per_window": ev,
                                      "c_per_pair": evc, "breakeven_split_rate": be}
    return out


def cli(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", action="store_true", help="evaluate entry rules on matched historical taker pairs")
    a = ap.parse_args(argv)
    W, b, med, resid = main()
    if a.hist:
        split = np.mean([x["k_yes"] != x["p_up"] for x in W])
        res = evaluate(hist(W, b, med, resid), float(split))
        Path("reports").mkdir(exist_ok=True)
        Path("reports/basis_study.json").write_text(json.dumps(res, indent=1))
        print("\nsaved reports/basis_study.json")


if __name__ == "__main__":
    cli()
