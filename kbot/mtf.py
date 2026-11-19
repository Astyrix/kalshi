"""Multi-timeframe pattern learner: 1m + 5m + 15m candles, named and learned
candlestick patterns, order flow, and (optionally) RSI / MACD / EMA.

What it learns, all scored walk-forward (retrained before each test block on
everything earlier, never on what it is scored on):

  1. How the 15-minute market settles (P(UP)), checked against the market price.
  2. Which candlestick patterns on which timeframe actually move price next.
  3. Scalp entries: buy the side whose predicted round trip beats costs.
  4. Scalp exits: hold while the model predicts the bid keeps rising, sell on
     the predicted reversal.
  5. Ablations: does each feature group (indicators, named patterns, raw candle
     shapes, the 5m / 15m cross-check, order flow) help on unseen data?

  python -m kbot.mtf --days 70
  python -m kbot.mtf --days 70 --no-indicators
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from .data import MaxyClient, load_flow
from .patterns import PATTERNS, TIMEFRAMES, Series, detect, mtf_features, vol_per_sec
from .strategy import taker_fee
from .sweep import load_cached

QTY = 10
HOLDS = (1, 2, 3, 5)


# ---- dataset -----------------------------------------------------------------
def flow_feats(flow, t, open_ts, mid):
    if not flow:
        return {}
    y60 = n60 = y300 = n300 = ya = na = 0.0
    px60 = vol60 = 0.0
    for b, yv, nv, _, vw in flow:
        if b > t - 10:          # bucket must have fully finished by t
            break
        if b >= open_ts:
            ya += yv; na += nv
        if b > t - 310:
            y300 += yv; n300 += nv
        if b > t - 70:
            y60 += yv; n60 += nv
            if vw is not None:
                px60 += vw * (yv + nv); vol60 += yv + nv
    el = max((t - open_ts) / 60, 1)
    f = {
        "flow_imb60": (y60 - n60) / max(y60 + n60, 1),
        "flow_imb300": (y300 - n300) / max(y300 + n300, 1),
        "flow_imb_all": (ya - na) / max(ya + na, 1),
        "flow_vol_ratio": (y60 + n60) / max((ya + na) / el, 1),
        "flow_vwap_vs_mid": (px60 / vol60 - mid) if vol60 else 0.0,
    }
    return f


def build(windows, series, client=None, indicators=True):
    rows = []
    for wi, w in enumerate(windows):
        if w.result not in ("yes", "no"):
            continue
        q = [(t, yb, ya) for t, yb, ya, _ in w.quotes
             if yb is not None and ya is not None and 0.005 < yb <= ya < 0.995 and t < w.close_ts]
        flow = load_flow(client, w.ticker) if client else None
        up_won = w.result == "yes"
        for i in range(3, len(q)):
            t, yb, ya = q[i]
            f = mtf_features(series, t, indicators)
            if f is None:
                continue
            price = series.at(t)
            sig = f["vol_bp"] / 1e4
            mid = (yb + ya) / 2
            f.update({
                "dist_sigma": (price - w.strike) / max(sig * price * math.sqrt(max(w.close_ts - t, 1)), 1e-6),
                "tau": (w.close_ts - t) / 900,
                "mid": mid, "spread": ya - yb,
                "mid_chg1": mid - (q[i - 1][1] + q[i - 1][2]) / 2,
                "mid_chg3": mid - (q[i - 3][1] + q[i - 3][2]) / 2,
            })
            f.update(flow_feats(flow, t, w.open_ts, mid))
            tg = {"settle_up": float(up_won)}
            for h in HOLDS:
                if i + h < len(q) and q[i + h][0] - t <= 60 * h + 5:
                    _, yb2, ya2 = q[i + h]
                    tg[f"up_rt{h}"], tg[f"dn_rt{h}"] = yb2 - ya, yb - ya2
                    tg[f"up_bd{h}"], tg[f"dn_bd{h}"] = yb2 - yb, ya - ya2
            rows.append({"w": wi, "t": t, "f": f, "y": tg, "q": (yb, ya), "has_flow": bool(flow)})
    return rows


def matrix(rows, names):
    return np.array([[r["f"].get(n, np.nan) for n in names] for r in rows], dtype=float)


def target(rows, key):
    return np.array([r["y"].get(key, np.nan) for r in rows], dtype=float)


def reg(X, y):
    import lightgbm as lgb
    ok = ~np.isnan(y)
    m = lgb.LGBMRegressor(n_estimators=200, learning_rate=0.04, num_leaves=15, min_child_samples=80,
                          subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=2.0,
                          random_state=0, verbose=-1)
    m.fit(X[ok], y[ok])
    return m


def clf(X, y):
    import lightgbm as lgb
    m = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.04, num_leaves=15, min_child_samples=80,
                           subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=2.0,
                           random_state=0, verbose=-1)
    m.fit(X, y)
    return m


def cost(entry, exit_, slip):
    return taker_fee(min(max(entry, 0.01), 0.99), QTY) + taker_fee(min(max(exit_, 0.01), 0.99), QTY) + 2 * slip


# ---- walk-forward ------------------------------------------------------------
def walk_forward(rows, names, n_windows, folds=5, start=0.5):
    """Predictions for every row in the test region, each from a model trained
    only on earlier windows."""
    X = matrix(rows, names)
    W = np.array([r["w"] for r in rows])
    keys = ["settle_up"] + [f"{s}_rt{h}" for s in ("up", "dn") for h in HOLDS] + \
           [f"{s}_bd{h}" for s in ("up", "dn") for h in (1, 2)]
    pred = {k: np.full(len(rows), np.nan) for k in keys}
    edges = [int(n_windows * (start + (1 - start) * k / folds)) for k in range(folds + 1)]
    for a, b in zip(edges, edges[1:]):
        tr, te = W < a, (W >= a) & (W < b)
        if te.sum() == 0:
            continue
        for k in keys:
            y = target(rows, k)
            if k == "settle_up":
                pred[k][te] = clf(X[tr], y[tr]).predict_proba(X[te])[:, 1]
            else:
                pred[k][te] = reg(X[tr], y[tr]).predict(X[te])
        print(f"  fold windows {a}-{b}: trained on {tr.sum()} points, scored {te.sum()}", flush=True)
    return pred, edges[0]


def scalp(rows, pred, test_start, margin, max_hold, windows, slip=0.01):
    by_w = {}
    for i, r in enumerate(rows):
        if r["w"] >= test_start:
            by_w.setdefault(r["w"], []).append(i)
    trades = []
    for wi, ids in by_w.items():
        w = windows[wi]
        k = 0
        while k < len(ids):
            i = ids[k]
            yb, ya = rows[i]["q"]
            best = None
            for side, ask in (("up", ya), ("dn", round(1 - yb, 4))):
                for h in HOLDS:
                    g = pred[f"{side}_rt{h}"][i]
                    if np.isnan(g):
                        continue
                    e = g - cost(ask + slip, ask + g, slip)
                    if e > margin and (best is None or e > best[0]):
                        best = (e, side)
            if best is None:
                k += 1
                continue
            side = best[1]
            ask = ya if side == "up" else round(1 - yb, 4)
            entry = ask + slip
            j, out = k, None
            while True:
                j += 1
                if j >= len(ids):
                    break
                nb, na = rows[ids[j]]["q"]
                bid = nb if side == "up" else round(1 - na, 4)
                held = j - k
                more = max(pred[f"{side}_bd1"][ids[j]], pred[f"{side}_bd2"][ids[j]])
                if held >= max_hold or not (more > 0.005):
                    out = bid - slip
                    break
            if out is None:                                # rode it to settlement
                won = (side == "up") == (w.result == "yes")
                trades.append(((1.0 if won else 0.0) - entry - taker_fee(entry, QTY)) * QTY)
                break
            trades.append((max(out, 0) - entry - taker_fee(entry, QTY) - taker_fee(max(out, 0.01), QTY)) * QTY)
            k = j + 1
    return trades


# ---- pattern statistics ------------------------------------------------------------
def pattern_stats(series, split_ts):
    """For each named pattern on each timeframe: how far price moved over the
    next candle, in sigma units, before vs after split_ts."""
    out = []
    for name, tf in TIMEFRAMES.items():
        first = series.t0 + 1800
        first -= first % tf
        acc = {}
        agree = {}
        for t in range(first, series.t0 + len(series.v) - tf, tf):
            nxt, now = series.at(t + tf), series.at(t)
            if math.isnan(nxt) or math.isnan(now):
                continue
            sig = vol_per_sec(series.upto(t, 1800))
            mv = (nxt - now) / max(sig * now * math.sqrt(tf), 1e-6)
            cs, _ = series.candles(t, tf, 6)
            part = "train" if t < split_ts else "test"
            for p, v in detect(cs).items():
                if v:
                    acc.setdefault((p, part), []).append(mv)
            if name == "1m":
                f = mtf_features(series, t, indicators=False)
                if f:
                    agree.setdefault((int(f["tf_agree"]), part), []).append(mv)
        for p, d in PATTERNS.items():
            a, b = acc.get((p, "train"), []), acc.get((p, "test"), [])
            if len(a) < 30 or len(b) < 15:
                continue
            ta = np.mean(a) / (np.std(a) / math.sqrt(len(a)) + 1e-9)
            out.append({"tf": name, "pattern": p, "textbook": d, "n_train": len(a), "n_test": len(b),
                        "move_train": round(float(np.mean(a)), 3), "move_test": round(float(np.mean(b)), 3),
                        "t_train": round(float(ta), 2),
                        "up_rate_test": round(float(np.mean(np.array(b) > 0)), 3)})
        if name == "1m":
            for g in range(-3, 4):
                a, b = agree.get((g, "train"), []), agree.get((g, "test"), [])
                if b:
                    out.append({"tf": "1m+5m+15m", "pattern": f"trend_agree={g:+d}", "textbook": int(np.sign(g)),
                                "n_train": len(a), "n_test": len(b),
                                "move_train": round(float(np.mean(a)), 3) if a else None,
                                "move_test": round(float(np.mean(b)), 3),
                                "t_train": None, "up_rate_test": round(float(np.mean(np.array(b) > 0)), 3)})
    return out


# ---- main ----------------------------------------------------------------------------
GROUPS = {
    "all features": lambda n: True,
    "no indicators (RSI/MACD/EMA off)": lambda n: not any(n.endswith(s) for s in ("_rsi", "_macd", "_ema_gap")),
    "no named patterns": lambda n: "_pat_" not in n,
    "no raw candle shapes": lambda n: not any(x in n for x in ("_c1_", "_c2_", "_c3_", "_c4_", "_now_")),
    "1m only (no 5m/15m cross-check)": lambda n: not (n.startswith("5m_") or n.startswith("15m_") or n.startswith("tf_")),
    "no order flow": lambda n: not n.startswith("flow_"),
    "contract price only": lambda n: n in ("mid", "spread", "mid_chg1", "mid_chg3", "tau", "dist_sigma", "vol_bp"),
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", default="BTC")
    ap.add_argument("--days", type=float, default=70)
    ap.add_argument("--no-indicators", action="store_true")
    ap.add_argument("--flow-only", action="store_true", help="only windows with order-flow data")
    ap.add_argument("--slippage", type=float, default=0.01)
    ap.add_argument("--skip-patterns", action="store_true")
    ap.add_argument("--skip-ablation", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.time()
    client = MaxyClient(a.coin)
    ws = load_cached(a.coin, a.days)
    series = Series(ws)
    if a.flow_only:
        ws = [w for w in ws if load_flow(client, w.ticker) is not None]
    print(f"{len(ws)} windows, building features...", flush=True)
    rows = build(ws, series, client, indicators=not a.no_indicators)
    names = sorted({k for r in rows for k in r["f"]})
    n_flow = sum(r["has_flow"] for r in rows)
    print(f"{len(rows)} decision points, {len(names)} features "
          f"({n_flow} points have order flow) [{time.time() - t0:.0f}s]\n", flush=True)
    report = {"windows": len(ws), "points": len(rows), "features": len(names)}

    # 1-4: walk-forward with all features
    pred, test_start = walk_forward(rows, names, len(ws))
    te = np.array([r["w"] >= test_start for r in rows])
    y = target(rows, "settle_up")[te]
    p = pred["settle_up"][te]
    mid = np.array([(r["q"][0] + r["q"][1]) / 2 for r in rows])[te]
    bm, bk = float(np.mean((p - y) ** 2)), float(np.mean((mid - y) ** 2))
    acc_m, acc_k = float(np.mean((p > .5) == (y > .5))), float(np.mean((mid > .5) == (y > .5)))
    # does blending model + market beat the market? (a real edge would show here)
    print("\n15-MINUTE OUTCOME PREDICTION (unseen windows)")
    print(f"  model:  right {acc_m:.1%}, Brier {bm:.4f}")
    print(f"  market: right {acc_k:.1%}, Brier {bk:.4f}   (lower Brier is better)")
    disagree = np.abs(p - mid) > 0.10
    if disagree.sum():
        print(f"  when model and market disagree by >10 points ({disagree.sum()} cases): "
              f"model closer {np.mean(np.abs(p - y)[disagree] < np.abs(mid - y)[disagree]):.1%} of the time")
    report["settle"] = dict(model_acc=acc_m, model_brier=bm, market_acc=acc_k, market_brier=bk)

    print("\nSCALP WITH LEARNED ENTRIES AND EXITS (unseen windows)")
    print(f"  {'min edge':>8} {'max hold':>8} | {'trades':>6} {'win%':>6} {'P&L $':>9} {'avg $':>7}")
    report["scalp"] = []
    for margin in (0.01, 0.02, 0.04, 0.06):
        for mh in (1, 3, 5, 15):
            tr = scalp(rows, pred, test_start, margin, mh, ws, a.slippage)
            wr = sum(x > 0 for x in tr) / len(tr) if tr else 0
            print(f"  {margin:8.2f} {mh:8d} | {len(tr):6d} {wr:6.1%} {sum(tr):9.2f} {sum(tr) / max(len(tr), 1):+7.3f}")
            report["scalp"].append(dict(margin=margin, max_hold=mh, trades=len(tr), win_rate=wr, pnl=sum(tr)))

    # 5: ablations on one split (cheaper): settlement Brier + best scalp cell
    if not a.skip_ablation:
        print("\nWHAT EACH FEATURE GROUP ADDS (train first 65%, test last 35%)")
        cut = int(len(ws) * 0.65)
        W = np.array([r["w"] for r in rows])
        tr_, te_ = W < cut, W >= cut
        yy = target(rows, "settle_up")
        mid_all = np.array([(r["q"][0] + r["q"][1]) / 2 for r in rows])
        print(f"  {'features':34} {'#':>4} | {'outcome Brier':>13} {'vs market':>9} | "
              f"{'1-min UP move: corr':>19}")
        report["ablation"] = []
        for gname, keep in GROUPS.items():
            sub = [n for n in names if keep(n)]
            if not sub:
                continue
            X = matrix(rows, sub)
            pp = clf(X[tr_], yy[tr_]).predict_proba(X[te_])[:, 1]
            b = float(np.mean((pp - yy[te_]) ** 2))
            ym = target(rows, "up_bd1")
            ok = te_ & ~np.isnan(ym)
            pm = reg(X[tr_], ym[tr_]).predict(X[ok])
            c = float(np.corrcoef(pm, ym[ok])[0, 1])
            print(f"  {gname:34} {len(sub):4d} | {b:13.4f} {b - float(np.mean((mid_all[te_] - yy[te_]) ** 2)):+9.4f} | {c:+19.3f}")
            report["ablation"].append(dict(group=gname, n=len(sub), brier=b, corr_bid_move=c))

    # 2: pattern statistics
    if not a.skip_patterns:
        split_ts = ws[int(len(ws) * 0.6)].close_ts
        print("\nCANDLESTICK PATTERNS: average move over the next candle, in sigma units", flush=True)
        stats = pattern_stats(series, split_ts)
        stats.sort(key=lambda s: -abs(s["t_train"] or 0))
        print(f"  {'tf':>9} {'pattern':22} {'book':>4} | {'n train':>7} {'move':>6} {'t':>6} | "
              f"{'n test':>6} {'move':>6} {'up%':>5}  holds up?")
        for s in stats:
            if s["t_train"] is None:
                continue
            same = (s["move_train"] > 0) == (s["move_test"] > 0)
            flag = "yes" if same and abs(s["t_train"]) >= 2 else ("weak" if same else "NO")
            print(f"  {s['tf']:>9} {s['pattern']:22} {s['textbook']:+4d} | {s['n_train']:7d} {s['move_train']:+6.2f} "
                  f"{s['t_train']:+6.1f} | {s['n_test']:6d} {s['move_test']:+6.2f} {s['up_rate_test']:5.1%}  {flag}")
        print("\n  1m+5m+15m trend agreement -> next 1-minute move (test):")
        for s in stats:
            if s["t_train"] is None:
                print(f"    {s['pattern']:16} n={s['n_test']:6d}  move {s['move_test']:+.3f} sigma  up {s['up_rate_test']:.1%}")
        report["patterns"] = stats

    Path("reports").mkdir(exist_ok=True)
    Path("reports/mtf.json").write_text(json.dumps(report, indent=2, default=float))
    print(f"\ndone in {time.time() - t0:.0f}s; full report in reports/mtf.json")


if __name__ == "__main__":
    main()
