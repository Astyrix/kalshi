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
from .trader import BARRIERS, QTY, Context, barrier_pnl, build_arrays, lean_ok, lgb_reg

KEYS = [f"{s}_{tp}_{sl}" for s in ("up", "dn") for tp, sl in BARRIERS]


def quotes(w):
    """Every minute before close, unfiltered (see trader.all_quotes): label code
    walks the real future, including books that went one-sided."""
    from .trader import all_quotes
    return all_quotes(w)


def trail_pnl(q, i, side, tp, sl, w, trail=None, slip=0.01):
    """Like barrier_pnl, but once the bid reaches +tp, hold and sell when the
    bid falls `trail` below its best level since (default trail = tp)."""
    trail = tp if trail is None else trail
    yb, ya = q[i][1], q[i][2]
    entry = (ya if side == "up" else round(1 - yb, 4)) + slip
    fee_in = taker_fee(min(entry, 0.99), QTY)
    peak, armed, last = None, False, None
    for j in range(i + 1, len(q)):
        b = max((q[j][1] if side == "up" else round(1 - q[j][2], 4)) - slip, 0.0)
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
        # reuse it unless much more history has arrived (live trading adds a few
        # markets an hour; rebuilding takes minutes). Markets are matched by ticker.
        if "tickers" in d and 0 <= len(ws) - d["n_windows"] < 500:
            by_t = {w.ticker: w for w in ws}
            if all(t in by_t for t in d["tickers"]):
                return d, [by_t[t] for t in d["tickers"]]
    series = Series(ws)
    ctx = Context(ws, series)
    d = build_arrays(ws, ctx, MaxyClient("BTC"))
    d.update(n_windows=len(ws), tickers=[w.ticker for w in ws])
    with open(p, "wb") as fh:
        pickle.dump(d, fh)
    return d, ws


def add_delayed_labels(d, ws, delay=1):
    """Labels where the order fills `delay` minutes after the decision, at that
    minute's ask: a stale quote at decision time can't be bought any more."""
    pos = {}
    out = {k: np.full(len(d["W"]), np.nan) for k in KEYS}
    ask_out = {s_: np.full(len(d["W"]), np.nan) for s_ in ("up", "dn")}
    for n, (wi, t) in enumerate(zip(d["W"], d["T"])):
        if wi not in pos:
            q = quotes(ws[wi])
            pos[wi] = (q, {qq[0]: j for j, qq in enumerate(q)})
        q, ix = pos[wi]
        i = ix.get(int(t))
        if i is None or i + delay >= len(q) - 1:
            continue
        j = i + delay
        from .trader import valid_quote
        if not valid_quote(q[j]):
            continue
        ask_out["up"][n], ask_out["dn"][n] = q[j][2], 1 - q[j][1]
        for k in KEYS:
            s_, tp, sl = k.split("_")
            out[k][n] = barrier_pnl(q, j, s_, float(tp), float(sl), ws[wi])
    return out, ask_out


def real_pnl(q, i, side, tp, sl, w, px, slip=0.01, fees=True):
    """P&L per contract when every fill must be confirmed by a real trade.

    Entry: limit buy at ask + slip, filled only if this side traded at or
    below it during the next minute (else 0.0: nothing happened). Exit: when
    the quoted bid crosses +TP/-SL (or at the last minute), a limit sell at
    bid - slip, filled only if this side traded at or above it in the minute
    after; otherwise keep holding and retry; nothing filled = settlement."""
    def side_rng(row):            # (low, high) traded price of OUR side in that minute
        _, o, h, l, c, v = row
        if not v or h is None or l is None:
            return None
        return (l, h) if side == "up" else (round(1 - h, 4), round(1 - l, 4))
    yb, ya = q[i][1], q[i][2]
    lim = (ya if side == "up" else round(1 - yb, 4)) + slip
    r = px.get(q[i][0] + 60)
    rng = side_rng(r) if r else None
    if rng is None or rng[0] > lim:
        return 0.0                                    # never filled
    entry = lim
    fee = (lambda x: taker_fee(x, QTY)) if fees else (lambda x: 0.0)
    fee_in = fee(min(entry, 0.99))
    for j in range(i + 1, len(q)):
        b = (q[j][1] if side == "up" else round(1 - q[j][2], 4)) - slip
        if b >= entry + tp or b <= entry - sl or j == len(q) - 1:
            r2 = px.get(q[j][0] + 60)
            rng2 = side_rng(r2) if r2 else None
            if rng2 is not None and rng2[1] >= b and b > 0:
                return b - entry - fee_in - fee(max(b, 0.01))
    won = (side == "up") == (w.result == "yes")
    return (1.0 if won else 0.0) - entry - fee_in


def maker_pnl(q, i, side, tp, sl, w, px, maker_rate=0.0175, through=0.0):
    """Resting-order version, conservative about queue position.

    Entry: rest a buy at our side's best bid for one minute; filled only if
    our side TRADED BELOW it during that minute (so even last in the queue
    would be filled). Else 0 (no trade). Take-profit: rest a sell at
    entry + tp; filled only if our side traded ABOVE it in a later minute.
    Stop-loss: when the quoted bid <= entry - sl, sell at bid - 1c as a
    taker. Otherwise hold to settlement. Maker fee = maker_rate * C*P*(1-P)."""
    import math
    def mfee(x):
        return math.ceil(round(maker_rate * QTY * x * (1 - x) * 100, 6)) / 100 / QTY if maker_rate else 0.0
    def rng(row):
        _, o, h, l, c, v = row
        if not v or h is None or l is None:
            return None
        return (l, h) if side == "up" else (round(1 - h, 4), round(1 - l, 4))
    bid = q[i][1] if side == "up" else round(1 - q[i][2], 4)
    if bid < 0.01:
        return 0.0
    r = px.get(q[i][0] + 60)
    g = rng(r) if r else None
    if g is None or not g[0] < bid - through:
        return 0.0                                   # never filled
    entry = bid
    cost = mfee(entry)
    target = round(entry + tp, 4)
    for j in range(i + 1, len(q)):
        # in time order: the quote at q[j] (stop check) comes before the trades
        # in the minute after it (take-profit check)
        b = (q[j][1] if side == "up" else round(1 - q[j][2], 4)) - 0.01
        if b <= entry - sl and b > 0:
            return b - entry - cost - taker_fee(max(b, 0.01), QTY)
        r2 = px.get(q[j][0] + 60)          # trades during the minute after quote j
        g2 = rng(r2) if r2 else None
        if g2 is not None and g2[1] > target + through and target < 0.99:
            return target - entry - cost - mfee(target)
    won = (side == "up") == (w.result == "yes")
    return (1.0 if won else 0.0) - entry - cost


def add_maker_labels(d, ws, maker_rate=0.0175, through=0.0):
    from .data import MaxyClient, load_px
    c = MaxyClient("BTC")
    pos = {}
    out = {k: np.full(len(d["W"]), np.nan) for k in KEYS}
    for n, (wi, t) in enumerate(zip(d["W"], d["T"])):
        if wi not in pos:
            rows = load_px(c, ws[wi].ticker)
            q = quotes(ws[wi])
            pos[wi] = (q, {qq[0]: j for j, qq in enumerate(q)},
                       {int(r[0]): r for r in rows} if rows else None)
        q, ix, px = pos[wi]
        i = ix.get(int(t))
        if i is None or px is None:
            continue
        for k in KEYS:
            s_, tp, sl = k.split("_")
            out[k][n] = maker_pnl(q, i, s_, float(tp), float(sl), ws[wi], px, maker_rate, through)
    return out


def add_real_labels(d, ws, slip=0.01, fees=True):
    from .data import MaxyClient, load_px
    c = MaxyClient("BTC")
    pos = {}
    out = {k: np.full(len(d["W"]), np.nan) for k in KEYS}
    have = 0
    for n, (wi, t) in enumerate(zip(d["W"], d["T"])):
        if wi not in pos:
            rows = load_px(c, ws[wi].ticker)
            q = quotes(ws[wi])
            pos[wi] = (q, {qq[0]: j for j, qq in enumerate(q)},
                       {int(r[0]): r for r in rows} if rows else None)
        q, ix, px = pos[wi]
        i = ix.get(int(t))
        if i is None or px is None:
            continue
        have += 1
        for k in KEYS:
            s_, tp, sl = k.split("_")
            out[k][n] = real_pnl(q, i, s_, float(tp), float(sl), ws[wi], px, slip, fees)
    return out, have


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

    def trade(self, pred, Y, margin=0.02, allow=None, brake=None):
        """Same rule as trader.py: best pair above margin, no new entry for 5 min.

        brake = (max_trades_per_24h, lookback_trades, min_lookback_pnl): pause
        new entries while more than max trades happened in the last 24 h, or
        while the last `lookback` trades lost more than `min_lookback_pnl`
        dollars (only trades that have already closed count; positions close
        within the 15-minute window)."""
        pnl, info = [], []
        recent_t: list[int] = []
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
                    t = int(self.d["T"][i])
                    if brake:
                        mx, lb, floor = brake
                        n24 = sum(1 for x in recent_t if x > t - 86400)
                        # P&L of the last `lb` trades that closed (entered >= 15 min ago)
                        closed = [p for (j, _), p in zip(info, pnl) if self.d["T"][j] <= t - 900][-lb:]
                        if n24 >= mx or (len(closed) >= lb and sum(closed) < floor):
                            continue
                    recent_t.append(t)
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
    e = Exp(d, d["n_windows"])
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
    if run("cheap_bag"):
        pb = e.walk(Y, seeds=(0, 1, 2, 3, 4))
        for m in (0.005, 0.01, 0.02):
            res[f"cheap_bag m{m}"] = e.report(f"cheap only + 5-seed bagging m{m}", *e.trade(pb, Y, m, cheap))
    if run("cheap_max"):
        for mx in (0.10, 0.15, 0.20, 0.30):
            al = {k: e.ask(k.split("_")[0]) <= mx for k in KEYS}
            res[f"cheap<={mx}"] = e.report(f"only <= {mx * 100:.0f}c, margin 0.01", *e.trade(base, Y, 0.01, al))
        res["cheap m0.005"] = e.report("only <= 25c, margin 0.005", *e.trade(base, Y, 0.005, cheap))
    if run("delay"):
        Yd, ask_d = add_delayed_labels(d, ws, 1)
        n_ok = np.sum(~np.isnan(Yd[KEYS[0]]))
        print(f"  delayed-fill labels for {n_ok} entry moments")
        # 1) same model and same decisions, but filled a minute later
        for mx in (0.25, 1.0):
            al = {k: e.ask(k.split("_")[0]) <= mx for k in KEYS}
            res[f"delay same-model <= {mx}"] = e.report(f"decide now, fill 1 min later, <= {mx * 100:.0f}c",
                                                       *e.trade(base, Yd, 0.01, al))
        # 2) a model trained on delayed-fill outcomes (learns only what survives the delay)
        pdl = e.walk(Yd)
        for m in (0.01, 0.02, 0.03):
            for mx in (0.25, 1.0):
                al = {k: (e.ask(k.split("_")[0]) <= mx) for k in KEYS}
                res[f"delay model m{m} <= {mx}"] = e.report(
                    f"trained on 1-min-late fills, m{m}, <= {mx * 100:.0f}c", *e.trade(pdl, Yd, m, al))
    if run("real"):
        Yr, have = add_real_labels(d, ws)
        print(f"  real-fill labels for {have}/{len(d['W'])} entry moments (rest have no traded-price data yet)")
        # only test windows that have traded-price data
        for mx in (0.25, 1.0):
            al = {k: (e.ask(k.split("_")[0]) <= mx) & ~np.isnan(Yr[k]) for k in KEYS}
            res[f"real old-model <= {mx}"] = e.report(f"current model, real fills, <= {mx * 100:.0f}c",
                                                      *e.trade(base, Yr, 0.01, al))
        pr = e.walk(Yr, train_mask={k: ~np.isnan(Yr[k]) for k in KEYS})
        for m in (0.005, 0.01, 0.02, 0.03):
            for mx in (0.25, 1.0):
                al = {k: (e.ask(k.split("_")[0]) <= mx) & ~np.isnan(Yr[k]) for k in KEYS}
                res[f"real model m{m} <= {mx}"] = e.report(
                    f"trained on REAL fills, m{m}, <= {mx * 100:.0f}c", *e.trade(pr, Yr, m, al))
    if run("costs"):
        # how much do costs decide it? same walk-forward, cheaper fills
        for slip, fees, label in ((0.01, True, "ask+1c / bid-1c + fees"), (0.0, True, "at ask / bid + fees"),
                                  (0.0, False, "at ask / bid, no fees")):
            Yc, _ = add_real_labels(d, ws, slip, fees)
            ok = {k: ~np.isnan(Yc[k]) for k in KEYS}
            pc = e.walk(Yc, train_mask=ok)
            for m in (0.0, 0.005, 0.01):
                for mx in (0.25, 1.0):
                    al = {k: (e.ask(k.split("_")[0]) <= mx) & ok[k] for k in KEYS}
                    res[f"costs {label} m{m} <= {mx}"] = e.report(f"{label}, m{m}, <= {mx * 100:.0f}c",
                                                                 *e.trade(pc, Yc, m, al))
    if run("maker"):
        for rate, thr, label in ((0.0175, 0.0, "maker, fee 1/4 taker"),
                                 (0.0175, 0.01, "maker, fee 1/4 taker, fill needs 1c through")):
            Ym = add_maker_labels(d, ws, rate, thr)
            filled = {k: ~np.isnan(Ym[k]) & (Ym[k] != 0.0) for k in KEYS}
            print(f"  {label}: entry fills {np.mean(filled[KEYS[0]]):.1%} of moments (up_0.03_0.03)")
            ok = {k: ~np.isnan(Ym[k]) for k in KEYS}
            pm = e.walk(Ym, train_mask=ok)
            for m in (0.005, 0.01, 0.02):
                for mx in (1.0,):
                    al = {k: (e.ask(k.split("_")[0]) <= mx) & ok[k] for k in KEYS}
                    pnl, info = e.trade(pm, Ym, m, al)
                    keep = [n for n, p in enumerate(pnl) if p != 0.0]       # orders that filled
                    res[f"{label} m{m} <= {mx}"] = e.report(
                        f"{label}, m{m}, <= {mx * 100:.0f}c (filled only)",
                        pnl[keep] if len(keep) else np.array([]), [info[n] for n in keep])
    if run("hours"):
        hr = (d["T"] // 3600) % 24
        for lo, hi in ((8, 12), (8, 16), (12, 16)):
            al = {k: cheap[k] & ~((hr >= lo) & (hr < hi)) for k in KEYS}
            res[f"skip {lo}-{hi}"] = e.report(f"<=25c, skip {lo:02d}-{hi:02d} UTC, m0.01", *e.trade(base, Y, 0.01, al))
        # check the 08-12 hole on the TRAINING half too (not used to pick it)
        tr = d["W"] < e.ts
        print("  training-half check, avg 10-contract P&L of the best predicted setup by hour block:")
        for lo in range(0, 24, 4):
            m_ = tr & (hr >= lo) & (hr < lo + 4)
            y = np.nanmax(np.vstack([Y[k][m_] for k in KEYS]), axis=0)
            print(f"    {lo:02d}-{lo + 3:02d} UTC  hindsight-best avg ${np.nanmean(y) * QTY:+.3f}")
    Path("reports").mkdir(exist_ok=True)
    import json
    Path("reports/experiments.json").write_text(json.dumps(res, indent=1))
    print(f"\ndone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
