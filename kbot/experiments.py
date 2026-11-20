"""Out-of-sample tests of ideas for the day-trader scalp, all on the same data
and the same walk-forward folds as kbot/trader.py so results compare fairly.

  python -m kbot.experiments --days 70 [--only bagging,trail]

Ideas tested:
  baseline     the live model (lean candles, no flow / calendar)
  bagging      average 5 models with different random seeds (less noise)
  cheap_only   only trade contracts priced under 25c (where the profit was)
  cheap_model  a specialist trained only on cheap entries
  trail        after the take-profit is hit, keep holding and trail a stop
               instead of selling at once (cheap contracts can run 5c -> 90c)
  no_mid       never buy contracts priced 40-60c (the fee is highest there)

The feature table is cached in data/rows-<days>.pkl after the first build.
"""
from __future__ import annotations

import argparse
import math
import pickle
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from .data import MaxyClient
from .patterns import Series
from .strategy import taker_fee
from .sweep import load_cached
from .trader import BARRIERS, QTY, Context, barrier_pnl, build, lean_ok, lgb_reg

KEYS = [f"{s}_{tp}_{sl}" for s in ("up", "dn") for tp, sl in BARRIERS]


def quotes(w):
    return [(t, yb, ya) for t, yb, ya, _ in w.quotes
            if yb is not None and ya is not None and 0.005 < yb <= ya < 0.995 and t < w.close_ts]


def trail_pnl(q, i, side, tp, sl, w, trail=None, slip=0.01):
    """Like barrier_pnl, but once the bid reaches +tp, hold and sell when the
    bid falls `trail` below its best level since (default trail = tp)."""
    trail = tp if trail is None else trail
    yb, ya = q[i][1], q[i][2]
    entry = (ya if side == "up" else round(1 - yb, 4)) + slip
    fee_in = taker_fee(min(entry, 0.99), QTY)
    peak, armed, last = None, False, None
    for j in range(i + 1, len(q)):
        b = (q[j][1] if side == "up" else round(1 - q[j][2], 4)) - slip
        last = b
        if not armed:
            if b <= entry - sl:
                return b - entry - fee_in - taker_fee(max(b, 0.01), QTY)
            if b >= entry + tp:
                armed, peak = True, b
            continue
        peak = max(peak, b)
        if b <= peak - trail:
            return b - entry - fee_in - taker_fee(max(b, 0.01), QTY)
    if last is None:
        won = (side == "up") == (w.result == "yes")
        return (1.0 if won else 0.0) - entry - fee_in
    if armed and last >= 0.97:
        # still running into the close: settle instead of paying to sell
        won = (side == "up") == (w.result == "yes")
        return (1.0 if won else 0.0) - entry - fee_in
    return last - entry - fee_in - taker_fee(max(last, 0.01), QTY)


def dataset(days):
    p = Path(f"data/rows-{int(days)}.pkl")
    ws = load_cached("BTC", days)
    if p.exists():
        with open(p, "rb") as fh:
            d = pickle.load(fh)
        if d["n_windows"] == len(ws):
            return d, ws
    series = Series(ws)
    ctx = Context(ws, series)
    rows = build(ws, ctx, MaxyClient("BTC"))
    names = sorted({k for r in rows for k in r["f"]})
    d = {"n_windows": len(ws), "names": names,
         "X": np.array([[r["f"].get(n, np.nan) for n in names] for r in rows], dtype=np.float32),
         "W": np.array([r["w"] for r in rows]), "T": np.array([r["t"] for r in rows]),
         "B": np.array([r["q"][0] for r in rows]), "A": np.array([r["q"][1] for r in rows]),
         "Y": {k: np.array([r["y"].get(k, np.nan) for r in rows]) for k in KEYS + ["idx_barrier"]}}
    with open(p, "wb") as fh:
        pickle.dump(d, fh)
    return d, ws


def add_trail_labels(d, ws, trail_mult=1.0):
    pos = {}
    out = {k: np.full(len(d["W"]), np.nan) for k in KEYS}
    for n, (wi, t) in enumerate(zip(d["W"], d["T"])):
        if wi not in pos:
            q = quotes(ws[wi])
            pos[wi] = (q, {qq[0]: j for j, qq in enumerate(q)})
        q, ix = pos[wi]
        i = ix.get(int(t))
        if i is None:
            continue
        for k in KEYS:
            s, tp, sl = k.split("_")
            out[k][n] = trail_pnl(q, i, s, float(tp), float(sl), ws[wi], float(tp) * trail_mult)
    return out


class Exp:
    def __init__(self, d, n_windows, folds=5, start=0.5, drop=("flow", "calendar")):
        self.d = d
        self.cols = [j for j, n in enumerate(d["names"])
                     if n.split("__")[0] not in drop and lean_ok(n)]
        self.X = d["X"][:, self.cols].astype(float)
        self.W = d["W"]
        self.edges = [int(n_windows * (start + (1 - start) * k / folds)) for k in range(folds + 1)]
        self.ts = self.edges[0]

    def ask(self, side):
        return self.d["A"] if side == "up" else 1 - self.d["B"]

    def walk(self, Y, train_mask=None, seeds=(0,)):
        pred = {k: np.full(len(self.W), np.nan) for k in KEYS}
        for lo, hi in zip(self.edges, self.edges[1:]):
            tr, te = self.W < lo, (self.W >= lo) & (self.W < hi)
            for k in KEYS:
                m_tr = tr if train_mask is None else tr & train_mask[k]
                ps = []
                for s in seeds:
                    ps.append(lgb_reg(self.X[m_tr], Y[k][m_tr], seed=s).predict(self.X[te]))
                pred[k][te] = np.mean(ps, axis=0)
        return pred

    def trade(self, pred, Y, margin=0.02, allow=None):
        """Same rule as trader.py: best pair above margin, no new entry for 5 min."""
        pnl, info = [], []
        by_w = defaultdict(list)
        for i in np.where(self.W >= self.ts)[0]:
            by_w[self.W[i]].append(i)
        for wi, ids in by_w.items():
            busy = -1
            for i in ids:
                if self.d["T"][i] <= busy:
                    continue
                cands = [(k, pred[k][i]) for k in KEYS if allow is None or allow[k][i]]
                if not cands:
                    continue
                k, v = max(cands, key=lambda kv: kv[1])
                if v > margin and not math.isnan(Y[k][i]):
                    pnl.append(Y[k][i] * QTY)
                    info.append((i, k))
                    busy = self.d["T"][i] + 300
        return np.array(pnl), info

    def report(self, name, pnl, info):
        if not len(pnl):
            print(f"  {name:34} no trades", flush=True)
            return {}
        # by week of the test region, and without the 10 best trades
        wk = defaultdict(float)
        for (i, _), p in zip(info, pnl):
            wk[int(self.d["T"][i] // (7 * 86400))] += p
        pos_weeks = sum(v > 0 for v in wk.values())
        top = np.sort(pnl)[::-1]
        r = {"trades": len(pnl), "win": float(np.mean(pnl > 0)), "pnl": float(pnl.sum()),
             "ex_top10": float(top[10:].sum()), "weeks_pos": f"{pos_weeks}/{len(wk)}"}
        print(f"  {name:34} {r['trades']:5d} trades  win {r['win']:5.1%}  P&L ${r['pnl']:8.2f}  "
              f"w/o top-10 ${r['ex_top10']:8.2f}  weeks+ {r['weeks_pos']}", flush=True)
        return r


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=70)
    ap.add_argument("--only", default="")
    a = ap.parse_args(argv)
    only = set(filter(None, a.only.split(",")))
    run = lambda n: not only or n in only
    t0 = time.time()
    d, ws = dataset(a.days)
    e = Exp(d, len(ws))
    Y = d["Y"]
    print(f"{len(d['W'])} entry moments, {len(e.cols)} features, test from window {e.ts} "
          f"[{time.time() - t0:.0f}s]\n", flush=True)
    res = {}
    base = e.walk(Y)
    for m in (0.01, 0.02, 0.03):
        res[f"baseline m{m}"] = e.report(f"baseline margin {m}", *e.trade(base, Y, m))
    cheap = {k: e.ask(k.split("_")[0]) <= 0.25 for k in KEYS}
    if run("cheap_only"):
        for m in (0.01, 0.02):
            res[f"cheap_only m{m}"] = e.report(f"cheap only (<=25c) margin {m}", *e.trade(base, Y, m, cheap))
    if run("no_mid"):
        nomid = {k: np.abs(e.ask(k.split("_")[0]) - 0.5) > 0.1 for k in KEYS}
        res["no_mid"] = e.report("skip 40-60c contracts margin 0.02", *e.trade(base, Y, 0.02, nomid))
    if run("cheap_model"):
        pc = e.walk(Y, train_mask=cheap)
        for m in (0.01, 0.02):
            res[f"cheap_model m{m}"] = e.report(f"cheap specialist margin {m}", *e.trade(pc, Y, m, cheap))
    if run("trail"):
        for mult in (1.0, 2.0):
            Yt = add_trail_labels(d, ws, mult)
            pt = e.walk(Yt)
            for m in (0.02, 0.03):
                res[f"trail x{mult} m{m}"] = e.report(f"trailing exit (trail={mult}xTP) m{m}", *e.trade(pt, Yt, m))
            # trailing exits on the baseline's entries (same trades, different exit)
            res[f"trail x{mult} same entries"] = e.report(
                f"baseline entries, trail={mult}xTP exit", *e.trade(base, Yt, 0.02))
    if run("breakdown"):
        pnl, info = e.trade(base, Y, 0.02)
        names = [d["names"][j] for j in e.cols]
        tau_i = names.index("prob__tau")
        groups = defaultdict(list)
        from datetime import datetime, timezone
        for (i, k), p in zip(info, pnl):
            h = datetime.fromtimestamp(int(d["T"][i]), timezone.utc).hour
            groups[f"hour UTC {h // 4 * 4:02d}-{h // 4 * 4 + 3:02d}"].append(p)
            groups[f"minutes left {int(e.X[i, tau_i] * 15) // 3 * 3:2d}-{int(e.X[i, tau_i] * 15) // 3 * 3 + 2}"].append(p)
            a = e.ask(k.split("_")[0])[i]
            groups[f"entry {'<10c' if a < .1 else '10-25c' if a < .25 else '25-50c' if a < .5 else '50-75c' if a < .75 else '75c+'}"].append(p)
        print("\n  where the baseline (margin 0.02) makes and loses money:")
        for g in sorted(groups):
            v = np.array(groups[g])
            print(f"    {g:24} {len(v):4d} trades  win {np.mean(v > 0):5.1%}  P&L ${v.sum():8.2f}")
        print()
    if run("sizing"):
        # size by predicted edge: 5 contracts at the margin, up to 20 at 3x the margin
        for m in (0.02,):
            pnl, info = e.trade(base, Y, m)
            mult = np.array([min(max(base[k][i] / m, 0.5), 2.0) for i, k in info])
            res["sizing"] = e.report("size by edge (5-20 contracts) m0.02", pnl * mult, info)
    if run("bagging"):
        pb = e.walk(Y, seeds=(0, 1, 2, 3, 4))
        for m in (0.01, 0.02, 0.03):
            res[f"bagging m{m}"] = e.report(f"5-seed bagging margin {m}", *e.trade(pb, Y, m))
    Path("reports").mkdir(exist_ok=True)
    import json
    Path("reports/experiments.json").write_text(json.dumps(res, indent=1))
    print(f"\ndone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
