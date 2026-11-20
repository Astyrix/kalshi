"""Day-trader model: six skill groups as features, and stop/target outcomes
("does the bid hit +TP before -SL?") as what it learns.

Skill groups (each can be switched off to measure what it adds):
  prob     contract price = market probability; physics fair value; edge vs price
  vol      realized vol, typical 15-min move for this hour (learned only from the
           past), strike distance in units of that move, vol vs normal
  flow     Coinbase spot volume vs normal, big prints, spot-index basis,
           OKX funding rate and its change, Kalshi buyer/seller imbalance
  calendar hour, weekday/weekend, 8:30 ET data window, jobs Friday, US open
           and close, FOMC day and hours to the statement
  regime   efficiency ratio (trend vs chop) on 15m and 60m, range compression,
           label trend / range / chop
  price    VWAP (session + rolling 60m), session high/low, swing support and
           resistance from 5m candles, round numbers ($500 / $1000)
  candles  1m / 5m / 15m candle shapes and named patterns, timeframe agreement

Scalp: for each entry moment and each side, LightGBM predicts the P&L of
"buy at ask, exit at +TP or -SL (checked each minute), else sell before close"
for several TP/SL pairs. It enters the pair with the best predicted P&L if
it beats a margin. Walk-forward: retrained before each test block on
everything earlier.

  python -m kbot.trader --days 70
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import external as E
from .data import MaxyClient, load_flow
from .model import snapshot
from .mtf import flow_feats
from .patterns import Series, mtf_features, vol_per_sec
from .strategy import taker_fee
from .sweep import load_cached

QTY = 10
BARRIERS = [(0.03, 0.03), (0.05, 0.03), (0.05, 0.05), (0.08, 0.05), (0.12, 0.06), (0.20, 0.10)]

GROUPS = ("prob", "vol", "flow", "calendar", "regime", "price", "candles")


class Context:
    """Everything external, indexed for fast lookup at time t."""

    def __init__(self, windows, series: Series):
        self.series = series
        a, b = series.t0, series.t0 + len(series.v)
        self.cb = E.coinbase_minutes("BTC-USD", a, b)
        self._index_cb()
        self.fund_ts, self.fund = [], []
        try:
            f = E.okx_funding(a)
            self.fund_ts = [x[0] for x in f]
            self.fund = [x[1] for x in f]
        except Exception:
            pass                                  # funding only feeds the "flow" group
        self.fomc = E.fomc_decisions()
        # causal "typical move" tables: hour-of-day -> running |15m move| and volume
        self._prep_hour_tables()

    def _index_cb(self):
        self.cb_ts = np.array(sorted(self.cb))
        self.cb_close = np.array([self.cb[t][3] for t in self.cb_ts])
        self.cb_vol = np.array([self.cb[t][4] for t in self.cb_ts])
        self.cb_pv = np.cumsum(self.cb_close * self.cb_vol)
        self.cb_cv = np.cumsum(self.cb_vol)

    def refresh(self, now: int, minutes: int = 120):
        """Live: pull the latest Coinbase minutes and rebuild the lookup tables."""
        start = datetime.fromtimestamp(now - minutes * 60, timezone.utc)
        end = datetime.fromtimestamp(now, timezone.utc)
        rows = E._get("https://api.exchange.coinbase.com/products/BTC-USD/candles",
                      {"granularity": 60, "start": start.isoformat(), "end": end.isoformat()})
        for t, lo, hi, o, c, v in rows:
            if int(t) + 60 <= now:                # only finished minutes
                self.cb[int(t)] = (o, hi, lo, c, v)
        self._index_cb()
        self._prep_hour_tables()

    def _prep_hour_tables(self):
        s = self.series
        start = s.t0 + 900 - (s.t0 % 900)
        self.hm_ts = defaultdict(list)
        self.hm_cum = defaultdict(list)
        for t in range(start, s.t0 + len(s.v), 900):
            x0, x1 = s.at(t - 900), s.at(t)
            if math.isnan(x0) or math.isnan(x1):
                continue
            h = datetime.fromtimestamp(t, timezone.utc).hour
            prev = self.hm_cum[h][-1] if self.hm_cum[h] else 0.0
            self.hm_ts[h].append(t)
            self.hm_cum[h].append(prev + abs(x1 - x0))
        self.hv = defaultdict(lambda: ([], []))
        for i, t in enumerate(self.cb_ts):
            h = int(t // 3600 % 24)
            ts, cum = self.hv[h]
            ts.append(t)
            cum.append((cum[-1] if cum else 0.0) + self.cb_vol[i])

    def typical_move(self, t):
        """Average |15m move| at this UTC hour, using only data before t."""
        h = datetime.fromtimestamp(t, timezone.utc).hour
        ts, cum = self.hm_ts[h], self.hm_cum[h]
        k = bisect.bisect_left(ts, t)
        k0 = max(0, k - 120)                  # last ~30 days of that hour
        if k - k0 < 8:
            return math.nan
        return (cum[k - 1] - (cum[k0 - 1] if k0 else 0.0)) / (k - k0)

    def typical_vol_min(self, t):
        h = int(t // 3600 % 24)
        ts, cum = self.hv[h]
        k = bisect.bisect_left(ts, t)
        k0 = max(0, k - 1800)
        if k - k0 < 120:
            return math.nan
        return (cum[k - 1] - (cum[k0 - 1] if k0 else 0.0)) / (k - k0)

    def cb_idx(self, t):
        """Index of the last *finished* Coinbase minute before t."""
        return bisect.bisect_right(self.cb_ts, t - 60) - 1


def ratio(a, b):
    return a / b if b and not math.isnan(b) and b > 0 else math.nan


def features(ctx: Context, w, t, q_hist, flow) -> dict[str, float] | None:
    s = ctx.series
    base = mtf_features(s, t, indicators=False)
    if base is None:
        return None
    x = s.at(t)
    hist = s.upto(t, 3600)
    sig = vol_per_sec(hist[-1800:])
    tau = max(w.close_ts - t, 1)
    yb, ya = q_hist[-1][1], q_hist[-1][2]
    mid = (yb + ya) / 2
    f = {}
    # -- candles (from patterns.py)
    for k, v in base.items():
        f[f"candles__{k}"] = v
    # -- prob
    snap = snapshot([(tt, vv) for tt, vv in zip(range(t - len(hist) + 1, t + 1), hist) if not math.isnan(vv)],
                    w.strike, w.close_ts, t)
    f["prob__mid"] = mid
    f["prob__spread"] = ya - yb
    f["prob__fair"] = snap.p0 if snap else math.nan
    f["prob__fair_minus_mid"] = (snap.p0 - mid) if snap else math.nan
    f["prob__mid_chg1"] = mid - (q_hist[-2][1] + q_hist[-2][2]) / 2
    f["prob__mid_chg3"] = mid - (q_hist[-4][1] + q_hist[-4][2]) / 2 if len(q_hist) > 3 else 0.0
    f["prob__tau"] = tau / 900
    # -- vol
    tm = ctx.typical_move(t)
    f["vol__sigma_bp"] = sig * 1e4
    f["vol__typical_15m_move"] = tm
    f["vol__strike_dist_in_moves"] = ratio(x - w.strike, tm * math.sqrt(tau / 900)) if tm == tm else math.nan
    f["vol__now_vs_typical"] = ratio(sig * x * math.sqrt(900), tm)
    f["vol__1h_range_vs_typical"] = ratio(np.nanmax(hist) - np.nanmin(hist), tm)
    # -- flow
    i = ctx.cb_idx(t)
    if i >= 15:
        v1, v5, v15 = ctx.cb_vol[i], ctx.cb_vol[i - 4:i + 1].sum(), ctx.cb_vol[i - 14:i + 1].sum()
        tv = ctx.typical_vol_min(t)
        f["flow__cb_vol1_vs_typ"] = ratio(v1, tv)
        f["flow__cb_vol5_vs_typ"] = ratio(v5 / 5, tv)
        f["flow__cb_vol15_vs_typ"] = ratio(v15 / 15, tv)
        f["flow__big_print"] = ratio(ctx.cb_vol[i - 4:i + 1].max(), tv)
        f["flow__cb_basis"] = (ctx.cb_close[i] - s.at(int(ctx.cb_ts[i]) + 59)) / max(sig * x * 60 ** .5, 1e-6)
        # signed volume proxy: volume on up minutes minus down minutes, last 5
        d = np.sign(np.diff(ctx.cb_close[i - 5:i + 1]))
        f["flow__cb_signed_vol5"] = ratio(float((d * ctx.cb_vol[i - 4:i + 1]).sum()), v5)
    k = bisect.bisect_right(ctx.fund_ts, t) - 1
    if k >= 1:
        f["flow__funding"] = ctx.fund[k] * 1e4
        f["flow__funding_chg"] = (ctx.fund[k] - ctx.fund[k - 1]) * 1e4
    for kk, v in flow_feats(flow, t, w.open_ts, mid).items():
        f[f"flow__kalshi_{kk}"] = v
    # -- calendar
    for kk, v in E.calendar_feats(t, ctx.fomc).items():
        f[f"calendar__{kk}"] = v
    # -- regime
    for n, sec in (("15m", 900), ("60m", 3600)):
        h = hist[-sec:]
        h = h[~np.isnan(h)]
        if len(h) > 60:
            p = h[::15]
            er = abs(p[-1] - p[0]) / max(np.abs(np.diff(p)).sum(), 1e-9)
            f[f"regime__eff_{n}"] = er
            f[f"regime__dir_{n}"] = float(np.sign(p[-1] - p[0])) * er
    f["regime__compression"] = ratio(np.nanmax(hist[-900:]) - np.nanmin(hist[-900:]),
                                     np.nanmax(hist) - np.nanmin(hist))
    e60 = f.get("regime__eff_60m", math.nan)
    f["regime__label"] = 2.0 if e60 >= 0.35 else (1.0 if e60 >= 0.15 else 0.0)   # trend / range / chop
    # -- price action
    unit = max(sig * x * math.sqrt(300), 1e-6)
    if i >= 60:
        day0 = int(ctx.cb_ts[i]) - int(ctx.cb_ts[i]) % 86400
        j0 = bisect.bisect_left(ctx.cb_ts, day0)
        pv = ctx.cb_pv[i] - (ctx.cb_pv[j0 - 1] if j0 else 0)
        cv = ctx.cb_cv[i] - (ctx.cb_cv[j0 - 1] if j0 else 0)
        if cv > 0:
            f["price__vs_session_vwap"] = (x - pv / cv) / unit
        pv60 = ctx.cb_pv[i] - ctx.cb_pv[i - 60]
        cv60 = ctx.cb_cv[i] - ctx.cb_cv[i - 60]
        if cv60 > 0:
            f["price__vs_vwap60"] = (x - pv60 / cv60) / unit
    day_hist = s.upto(t, int(t % 86400) + 1)
    if len(day_hist) > 300:
        f["price__vs_session_high"] = (x - np.nanmax(day_hist)) / unit
        f["price__vs_session_low"] = (x - np.nanmin(day_hist)) / unit
    # swing highs/lows on 5m candles over the last 4h -> nearest support / resistance
    cs, _ = s.candles(t, 300, 48)
    highs = [c[1] for c in cs if c]
    lows = [c[2] for c in cs if c]
    piv_h = [highs[k] for k in range(2, len(highs) - 2) if highs[k] == max(highs[k - 2:k + 3])]
    piv_l = [lows[k] for k in range(2, len(lows) - 2) if lows[k] == min(lows[k - 2:k + 3])]
    levels = piv_h + piv_l
    above = [l for l in levels if l > x]
    below = [l for l in levels if l < x]
    f["price__to_resistance"] = (min(above) - x) / unit if above else 10.0
    f["price__to_support"] = (x - max(below)) / unit if below else 10.0
    f["price__strike_vs_resistance"] = (min(above) - w.strike) / unit if above else 10.0
    f["price__strike_vs_support"] = (w.strike - max(below)) / unit if below else 10.0
    for r in (500, 1000):
        near = round(x / r) * r
        f[f"price__to_round{r}"] = (x - near) / unit
    return f


def barrier_pnl(q, i, side, tp, sl, w, slip=0.01):
    """P&L per contract of: buy at ask now, exit when bid >= entry+tp or
    <= entry-sl (checked each minute), else sell at the last bid before close."""
    yb, ya = q[i][1], q[i][2]
    ask = ya if side == "up" else round(1 - yb, 4)
    entry = ask + slip
    fee_in = taker_fee(min(entry, 0.99), QTY)
    last = None
    for j in range(i + 1, len(q)):
        b = (q[j][1] if side == "up" else round(1 - q[j][2], 4)) - slip
        last = b
        if b >= entry + tp or b <= entry - sl:
            return b - entry - fee_in - taker_fee(max(b, 0.01), QTY)
    if last is None:
        won = (side == "up") == (w.result == "yes")
        return (1.0 if won else 0.0) - entry - fee_in
    return last - entry - fee_in - taker_fee(max(last, 0.01), QTY)


def build(windows, ctx, client):
    rows = []
    for wi, w in enumerate(windows):
        if w.result not in ("yes", "no"):
            continue
        q = [(t, yb, ya) for t, yb, ya, _ in w.quotes
             if yb is not None and ya is not None and 0.005 < yb <= ya < 0.995 and t < w.close_ts]
        flow = load_flow(client, w.ticker)
        for i in range(3, len(q) - 1):
            t = q[i][0]
            f = features(ctx, w, t, q[: i + 1], flow)
            if f is None:
                continue
            y = {f"{side}_{tp}_{sl}": barrier_pnl(q, i, side, tp, sl, w)
                 for side in ("up", "dn") for tp, sl in BARRIERS}
            idx = ctx.series.at(t)
            fut = [ctx.series.at(t + k) for k in range(1, 301)]
            tm = f.get("vol__typical_15m_move", math.nan)
            if tm == tm and not any(math.isnan(v) for v in fut):
                lvl = 0.5 * tm
                hit = 0
                for v in fut:
                    if v >= idx + lvl:
                        hit = 1
                        break
                    if v <= idx - lvl:
                        hit = -1
                        break
                y["idx_barrier"] = hit
            rows.append({"w": wi, "t": t, "f": f, "y": y, "q": q[i][1:]})
    return rows


def lgb_reg(X, y, seed=0):
    import lightgbm as lgb
    ok = ~np.isnan(y)
    m = lgb.LGBMRegressor(n_estimators=250, learning_rate=0.03, num_leaves=15, min_child_samples=100,
                          subsample=0.8, subsample_freq=1, colsample_bytree=0.6, reg_lambda=3.0,
                          random_state=seed, verbose=-1)
    return m.fit(X[ok], y[ok])


ORIGINAL_PATTERNS = {"doji", "hammer", "hanging_man", "shooting_star", "inverted_hammer", "bull_engulfing",
                     "bear_engulfing", "bull_harami", "bear_harami", "piercing", "dark_cloud", "morning_star",
                     "evening_star", "three_soldiers", "three_crows", "bull_marubozu", "bear_marubozu",
                     "inside_bar", "outside_bar", "tweezer_top", "tweezer_bottom"}
LEAN_CHART = {"breakout_up", "breakout_down", "failed_breakout_up", "failed_breakout_down"}


def lean_ok(name: str) -> bool:
    if "_chart_" in name:
        return name.split("_chart_")[1] in LEAN_CHART
    if "_pat_" in name:
        return name.split("_pat_")[1] in ORIGINAL_PATTERNS
    return True


def save_model(path, names, X, Y, keys, drop, n_windows, lean=False):
    """Fit every TP/SL pair (and the index stop-vs-target classifier) on all rows."""
    import pickle
    import lightgbm as lgb
    models = {k: lgb_reg(X, Y[k]) for k in keys}
    yb = Y["idx_barrier"]
    ok = ~np.isnan(yb) & (yb != 0)
    idx_clf = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=15, min_child_samples=100,
                                 verbose=-1, random_state=0).fit(X[ok], (yb[ok] > 0).astype(int))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump({"names": names, "models": models, "idx_clf": idx_clf, "keys": keys,
                     "barriers": BARRIERS, "drop": sorted(drop), "lean_candles": lean, "trained_windows": n_windows,
                     "trained_at": int(time.time())}, fh)
    print(f"saved model ({len(names)} features, {len(keys)} stop/target pairs) to {path}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=70)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--start", type=float, default=0.5)
    ap.add_argument("--skip-ablation", action="store_true")
    ap.add_argument("--drop", default="", help="comma-separated skill groups to leave out, e.g. flow,calendar")
    ap.add_argument("--save-model", help="train on ALL windows and save the model here (for kbot paper)")
    ap.add_argument("--no-walk", action="store_true", help="with --save-model: skip the walk-forward test")
    ap.add_argument("--lean-candles", action="store_true",
                    help="keep only the original candle patterns plus breakout chart patterns "
                         "(the full set adds ~120 mostly-noise inputs)")
    a = ap.parse_args(argv)
    drop = {g for g in a.drop.split(",") if g}
    t0 = time.time()
    client = MaxyClient("BTC")
    ws = [w for w in load_cached("BTC", a.days)]
    series = Series(ws)
    ctx = Context(ws, series)
    print(f"{len(ws)} windows; building day-trader features...", flush=True)
    rows = build(ws, ctx, client)
    names = sorted({k for r in rows for k in r["f"] if k.split("__")[0] not in drop
                    and not (a.lean_candles and not lean_ok(k))})
    X = np.array([[r["f"].get(n, np.nan) for n in names] for r in rows], dtype=float)
    W = np.array([r["w"] for r in rows])
    print(f"{len(rows)} entry moments, {len(names)} features [{time.time() - t0:.0f}s]", flush=True)
    for g in GROUPS:
        print(f"  {g:9} {sum(n.startswith(g + '__') for n in names):3d} features")
    keys = [f"{s}_{tp}_{sl}" for s in ("up", "dn") for tp, sl in BARRIERS]
    Y = {k: np.array([r["y"].get(k, np.nan) for r in rows]) for k in keys + ["idx_barrier"]}

    def walk(cols, which=keys):
        Xs = X[:, cols]
        pred = {k: np.full(len(rows), np.nan) for k in which}
        edges = [int(len(ws) * (a.start + (1 - a.start) * k / a.folds)) for k in range(a.folds + 1)]
        for lo, hi in zip(edges, edges[1:]):
            tr, te = W < lo, (W >= lo) & (W < hi)
            for k in which:
                pred[k][te] = lgb_reg(Xs[tr], Y[k][tr]).predict(Xs[te])
        return pred, edges[0]

    def trade(pred, test_start, margin):
        """One position at a time per window; pick the best predicted TP/SL pair."""
        pnl, info = [], []
        by_w = defaultdict(list)
        for i, r in enumerate(rows):
            if r["w"] >= test_start:
                by_w[r["w"]].append(i)
        for wi, ids in by_w.items():
            busy_until = -1
            for i in ids:
                if rows[i]["t"] <= busy_until:
                    continue
                k, v = max(((k, pred[k][i]) for k in keys), key=lambda kv: kv[1])
                if v > margin:
                    pnl.append(Y[k][i] * QTY)
                    info.append((i, k))
                    busy_until = rows[i]["t"] + 300     # don't stack entries
        return pnl, info

    all_cols = list(range(len(names)))
    if a.save_model:
        save_model(a.save_model, names, X, Y, keys, drop, len(ws), a.lean_candles)
        if a.no_walk:
            return
    pred, ts = walk(all_cols)
    te = W >= ts
    print(f"\nTEST REGION: windows {ts}-{len(ws)} ({te.sum()} entry moments), each fold "
          f"trained only on earlier windows\n")

    # 1. how good are the stop/target predictions?
    print("STOP / TARGET PREDICTIONS (does predicted P&L line up with what happened?)")
    print(f"  {'pair':18} {'corr':>6} | top-10% predicted: {'avg real P&L/contract':>22}")
    for k in keys:
        p, y = pred[k][te], Y[k][te]
        ok = ~np.isnan(y)
        c = np.corrcoef(p[ok], y[ok])[0, 1]
        top = p[ok] >= np.quantile(p[ok], 0.9)
        print(f"  {k:18} {c:+6.3f} | {y[ok][top].mean():+22.4f}")

    # index barrier: will BRTI reach +half a typical move before -half within 5 min?
    yb = Y["idx_barrier"]
    ok = te & ~np.isnan(yb) & (yb != 0)
    if ok.sum() > 100:
        tr_ok = (W < ts) & ~np.isnan(yb) & (yb != 0)
        import lightgbm as lgb
        m = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=15, min_child_samples=100,
                               verbose=-1, random_state=0).fit(X[tr_ok], (yb[tr_ok] > 0).astype(int))
        pp = m.predict_proba(X[ok])[:, 1]
        acc = np.mean((pp > 0.5) == (yb[ok] > 0))
        conf = np.abs(pp - 0.5) >= 0.15
        print(f"\nINDEX TARGET vs STOP (+/- half a typical 15m move, next 5 min): right {acc:.1%}"
              f"; on confident calls (>=65%) right {np.mean((pp[conf] > 0.5) == (yb[ok][conf] > 0)):.1%} "
              f"({conf.sum()} calls)")

    # 2. scalp results
    print("\nSCALP (enter the TP/SL pair with best predicted P&L, if above margin)")
    print(f"  {'margin':>7} | {'trades':>6} {'win%':>6} {'P&L $':>9} {'avg $':>7}")
    best = None
    out = {"scalp": []}
    for margin in (0.0, 0.01, 0.02, 0.03, 0.05):
        pnl, info = trade(pred, ts, margin)
        wr = np.mean(np.array(pnl) > 0) if pnl else 0
        print(f"  {margin:7.2f} | {len(pnl):6d} {wr:6.1%} {sum(pnl):9.2f} {np.mean(pnl) if pnl else 0:+7.3f}")
        out["scalp"].append(dict(margin=margin, trades=len(pnl), win_rate=float(wr), pnl=float(sum(pnl))))
        if pnl and (best is None or margin == 0.02):
            best = (margin, pnl, info)
        Path("reports").mkdir(exist_ok=True)
        Path(f"reports/trader_trades_m{margin:.2f}.json").write_text(json.dumps([
            {"t": rows[i]["t"], "window": rows[i]["w"], "pair": k, "pnl": float(pp),
             "yes_bid": rows[i]["q"][0], "yes_ask": rows[i]["q"][1]} for (i, k), pp in zip(info, pnl)]))

    # 3. results by regime / calendar (margin 0.02)
    if best:
        margin, pnl, info = best
        print(f"\nBREAKDOWN at margin {margin:.2f}:")
        groups = defaultdict(list)
        ri = names.index("regime__label")
        for (i, k), p in zip(info, pnl):
            lab = {0: "chop", 1: "range", 2: "trend"}.get(int(X[i, ri]) if X[i, ri] == X[i, ri] else -1, "?")
            groups[f"regime={lab}"].append(p)
            for c in ("calendar__cal_us_session", "calendar__cal_weekend", "calendar__cal_830_window",
                      "calendar__cal_fomc_day"):
                if c in names and X[i, names.index(c)] == 1:
                    groups[c.split("__cal_")[1]].append(p)
            groups[f"pair={k}"].append(p)
        for g in sorted(groups):
            v = groups[g]
            print(f"  {g:22} {len(v):5d} trades  win {np.mean(np.array(v) > 0):5.1%}  P&L ${sum(v):8.2f}")

    # 4. ablation: drop one skill group at a time
    if not a.skip_ablation:
        print("\nWHAT EACH SKILL ADDS (drop it, retrain, compare test P&L at margin 0.02)")
        sub_keys = [f"{s}_{tp}_{sl}" for s in ("up", "dn") for tp, sl in BARRIERS[:4]]
        base_pred, _ = walk(all_cols, sub_keys)
        def pnl_of(pr):
            pred_full = {k: pr.get(k, np.full(len(rows), -9.0)) for k in keys}
            return trade(pred_full, ts, 0.02)[0]
        b = pnl_of(base_pred)
        print(f"  {'all skills':22} {len(b):5d} trades  P&L ${sum(b):9.2f}")
        out["ablation"] = {"all": float(sum(b))}
        for g in GROUPS:
            cols = [j for j, n in enumerate(names) if not n.startswith(g + "__")]
            pr, _ = walk(cols, sub_keys)
            p = pnl_of(pr)
            print(f"  {'without ' + g:22} {len(p):5d} trades  P&L ${sum(p):9.2f}  ({sum(p) - sum(b):+.2f})")
            out["ablation"][g] = float(sum(p))
    Path("reports").mkdir(exist_ok=True)
    Path("reports/trader.json").write_text(json.dumps(out, indent=2))
    print(f"\ndone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
