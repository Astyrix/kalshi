"""Candlestick prediction + scalping.

1. Build 1-minute and 15-second candles from the BRTI index (second by second).
2. Describe each moment with candle features: body and wick sizes, engulfing,
   hammer / shooting star, doji, runs of same-colour candles, where price sits
   in its recent range, RSI, MACD, EMA 9/21, velocity and volatility, plus the
   Kalshi contract's own recent price moves.
3. Train gradient-boosted models (LightGBM) to predict:
     - index direction and move size ($) over the next 60 seconds
     - the scalp round trip for each side: buy at today's ask, sell at the bid
       `h` minutes later, for h = 1, 2, 3
4. Scalp: buy the side whose predicted round trip beats fees + spread by a
   margin, then sell when the model predicts no further gain (reversal) or the
   hold limit is reached.

Models are trained on the earlier part of the data and scored only on the
later, unseen part.

  python -m kbot.candles --days 30
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .model import ema, rsi, realized_vol
from .strategy import taker_fee
from .sweep import load_cached

QTY = 10
HOLDS = (1, 2, 3)


# ---- candles ------------------------------------------------------------------
def candles(pts, width):
    """OHLC candles of `width` seconds from (t, v) points: list of (t0, o, h, l, c)."""
    out = []
    for t, v in pts:
        b = t - t % width
        if out and out[-1][0] == b:
            o, h, l, _ = out[-1][1:]
            out[-1] = (b, o, max(h, v), min(l, v), v)
        else:
            out.append((b, v, v, v, v))
    return out


def candle_feats(cs, unit, prefix):
    """Shape features of the last 3 candles plus classic patterns."""
    f = {}
    for k in range(1, 4):
        if len(cs) < k:
            for n in ("ret", "body", "up_wick", "lo_wick", "range"):
                f[f"{prefix}{k}_{n}"] = 0.0
            continue
        _, o, h, l, c = cs[-k]
        f[f"{prefix}{k}_ret"] = (c - o) / unit
        f[f"{prefix}{k}_body"] = abs(c - o) / unit
        f[f"{prefix}{k}_up_wick"] = (h - max(o, c)) / unit
        f[f"{prefix}{k}_lo_wick"] = (min(o, c) - l) / unit
        f[f"{prefix}{k}_range"] = (h - l) / unit
    if len(cs) >= 2:
        (_, o1, h1, l1, c1), (_, o0, h0, l0, c0) = cs[-2], cs[-1]
        rng = max(h0 - l0, 1e-9)
        body = abs(c0 - o0)
        f[f"{prefix}_bull_engulf"] = float(c1 < o1 and c0 > o0 and c0 >= o1 and o0 <= c1)
        f[f"{prefix}_bear_engulf"] = float(c1 > o1 and c0 < o0 and c0 <= o1 and o0 >= c1)
        f[f"{prefix}_hammer"] = float((min(o0, c0) - l0) > 2 * body and (h0 - max(o0, c0)) < body + 1e-9)
        f[f"{prefix}_star"] = float((h0 - max(o0, c0)) > 2 * body and (min(o0, c0) - l0) < body + 1e-9)
        f[f"{prefix}_doji"] = float(body < 0.1 * rng)
    run = 0
    for _, o, _, _, c in reversed(cs):
        d = 1 if c > o else -1 if c < o else 0
        if d == 0 or (run and (d > 0) != (run > 0)):
            break
        run += d
    f[f"{prefix}_run"] = float(run)
    return f


def features(w, t, q_hist):
    """Feature dict at time t (only data printed at or before t)."""
    pts = [p for p in w.index if p[0] <= t]
    if len(pts) < 120:
        return None
    x = pts[-1][1]
    sig = realized_vol(pts)
    unit1 = max(sig * x * math.sqrt(60), 1e-6)
    unit15 = max(sig * x * math.sqrt(15), 1e-6)
    c60, c15 = candles(pts, 60), candles(pts, 15)
    f = {}
    f.update(candle_feats(c60, unit1, "m"))
    f.update(candle_feats(c15, unit15, "s"))
    closes = [c[4] for c in c15]
    e9, e21 = ema(closes, 9), ema(closes, 21)
    m12, m26 = ema(closes, 12), ema(closes, 26)
    macd = [a - b for a, b in zip(m12, m26)]
    f["ema_gap"] = (e9[-1] - e21[-1]) / unit15
    f["macd_hist"] = (macd[-1] - ema(macd, 9)[-1]) / unit15
    f["rsi15s"] = rsi(closes) / 100
    f["rsi1m"] = rsi([c[4] for c in c60]) / 100
    for s in (5, 15, 30, 60, 180):
        f[f"vel{s}"] = (x - pts[max(len(pts) - 1 - s, 0)][1]) / max(sig * x * math.sqrt(s), 1e-6)
    hi5 = max(v for _, v in pts[-300:])
    lo5 = min(v for _, v in pts[-300:])
    f["range_pos5m"] = (x - lo5) / max(hi5 - lo5, 1e-9)
    f["vol_bp"] = sig * 1e4
    f["vol_ratio"] = realized_vol(pts, 60) / max(sig, 1e-9)
    f["dist_sigma"] = (x - w.strike) / max(sig * x * math.sqrt(max(w.close_ts - t, 1)), 1e-6)
    f["tau"] = (w.close_ts - t) / 900
    # contract price action
    tq, yb, ya = q_hist[-1]
    f["mid"] = (yb + ya) / 2
    f["spread"] = ya - yb
    for k in (1, 2, 3):
        f[f"mid_chg{k}"] = (f["mid"] - (q_hist[-1 - k][1] + q_hist[-1 - k][2]) / 2) if len(q_hist) > k else 0.0
    return f


def build(windows):
    """Rows of (window_idx, t, features, targets)."""
    rows = []
    for wi, w in enumerate(windows):
        if w.result not in ("yes", "no") or not w.index:
            continue
        q = [(t, yb, ya) for t, yb, ya, _ in w.quotes
             if yb is not None and ya is not None and 0.005 < yb <= ya < 0.995 and t < w.close_ts]
        idx = {t: v for t, v in w.index}
        for i in range(2, len(q)):
            t, yb, ya = q[i]
            f = features(w, t, q[: i + 1])
            if f is None:
                continue
            tg = {}
            x0 = idx.get(t)
            x1 = idx.get(t + 60)
            tg["idx_move60"] = (x1 - x0) if (x0 is not None and x1 is not None) else None
            for h in HOLDS:
                if i + h < len(q) and q[i + h][0] - t <= 60 * h + 5:
                    _, yb2, ya2 = q[i + h]
                    tg[f"up_rt{h}"] = yb2 - ya          # buy YES at ask, sell at later bid
                    tg[f"dn_rt{h}"] = yb - ya2          # buy NO at (1-yb), sell at (1-ya2)
                else:
                    tg[f"up_rt{h}"] = tg[f"dn_rt{h}"] = None
            rows.append((wi, t, f, tg, (yb, ya)))
    return rows


def fit(X, y, seed=0):
    import lightgbm as lgb
    m = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=15,
                          min_child_samples=50, subsample=0.8, subsample_freq=1,
                          colsample_bytree=0.8, reg_lambda=1.0, random_state=seed, verbose=-1)
    m.fit(X, y)
    return m


def round_trip_cost(entry, exit_, slip):
    return taker_fee(entry, QTY) + taker_fee(max(exit_, 0.01), QTY) + 2 * slip


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", default="BTC")
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--train-frac", type=float, default=0.6)
    ap.add_argument("--slippage", type=float, default=0.01)
    a = ap.parse_args(argv)

    ws = load_cached(a.coin, a.days)
    rows = build(ws)
    names = sorted(rows[0][2])
    X = np.array([[r[2][n] for n in names] for r in rows], dtype=float)
    cut_w = int(len(ws) * a.train_frac)
    tr = np.array([r[0] < cut_w for r in rows])
    te = ~tr
    print(f"{len(ws)} windows, {len(rows)} decision points; "
          f"train on first {cut_w} windows, test on the last {len(ws) - cut_w}\n")
    report = {}

    # ---- 1. index direction + magnitude over the next 60s --------------------
    y = np.array([np.nan if r[3]["idx_move60"] is None else r[3]["idx_move60"] for r in rows])
    ok = ~np.isnan(y)
    m = fit(X[tr & ok], y[tr & ok])
    pred = m.predict(X[te & ok])
    act = y[te & ok]
    dir_acc = float(np.mean(np.sign(pred) == np.sign(act)))
    big = np.abs(pred) >= np.quantile(np.abs(pred), 0.8)
    dir_acc_big = float(np.mean(np.sign(pred[big]) == np.sign(act[big])))
    corr = float(np.corrcoef(pred, act)[0, 1])
    mae, mae0 = float(np.mean(np.abs(pred - act))), float(np.mean(np.abs(act)))
    print("INDEX PREDICTION (next 60s, unseen data)")
    print(f"  direction right {dir_acc:.1%} of the time (50% = coin flip); "
          f"{dir_acc_big:.1%} on its 20% most confident calls")
    print(f"  predicted vs actual move correlation {corr:+.3f}")
    print(f"  typical error ${mae:.2f} vs ${mae0:.2f} for always guessing 'no move'\n")
    imp = sorted(zip(m.feature_importances_, names), reverse=True)[:8]
    print("  most-used candle features: " + ", ".join(n for _, n in imp) + "\n")
    report["index"] = dict(direction_accuracy=dir_acc, direction_accuracy_top20=dir_acc_big,
                           corr=corr, mae=mae, mae_no_move=mae0)

    # ---- 2. scalp round-trip models ---------------------------------------
    models = {}
    for side in ("up", "dn"):
        for h in HOLDS:
            yy = np.array([np.nan if r[3][f"{side}_rt{h}"] is None else r[3][f"{side}_rt{h}"] for r in rows])
            ok = ~np.isnan(yy)
            models[(side, h)] = fit(X[tr & ok], yy[tr & ok])
    P = {k: mdl.predict(X) for k, mdl in models.items()}

    # ---- 3. scalp simulation on the unseen part --------------------------
    by_w = {}
    for i, r in enumerate(rows):
        by_w.setdefault(r[0], []).append(i)

    def simulate(margin, max_hold, windows_sel):
        trades = []
        for wi in windows_sel:
            ids = by_w.get(wi, [])
            k = 0
            while k < len(ids):
                i = ids[k]
                yb, ya = rows[i][4]
                best = None
                for side, ask in (("up", ya), ("dn", round(1 - yb, 4))):
                    for h in HOLDS[:max_hold]:
                        gain = P[(side, h)][i]
                        cost = round_trip_cost(ask + a.slippage, ask + gain, a.slippage)
                        edge = gain - cost
                        if edge > margin and (best is None or edge > best[0]):
                            best = (edge, side, h, ask)
                if best is None:
                    k += 1
                    continue
                _, side, h, ask = best
                entry = ask + a.slippage
                # hold while the model still expects more gain, up to h minutes
                j = k
                for step in range(1, h + 1):
                    if k + step >= len(ids):
                        break
                    j = k + step
                    nxt = ids[j]
                    nb, na = rows[nxt][4]
                    # expected bid change over the next minute = predicted
                    # round trip from here + the spread we'd not pay again
                    if step < h and P[(side, 1)][nxt] + (na - nb) > 0.005:
                        continue          # model expects further gain: keep holding
                    break
                if j == k:
                    k += 1
                    continue
                yb2, ya2 = rows[ids[j]][4]
                exit_bid = (yb2 if side == "up" else round(1 - ya2, 4)) - a.slippage
                fees = taker_fee(entry, QTY) + taker_fee(max(exit_bid, 0.01), QTY)
                trades.append(((max(exit_bid, 0) - entry - fees) * QTY, side, entry))
                k = j + 1
        return trades

    train_w = [i for i in range(cut_w)]
    test_w = [i for i in range(cut_w, len(ws))]
    print("SCALP (buy the side the model says will rise, sell at predicted reversal or hold limit)")
    print(f"  {'min edge':>8} {'max hold':>8} | {'train trades':>12} {'train P&L':>10} | "
          f"{'TEST trades':>11} {'win%':>6} {'TEST P&L':>9} {'avg':>7}")
    rows_out = []
    for margin in (0.0, 0.01, 0.02, 0.04):
        for mh in (1, 2, 3):
            t1 = simulate(margin, mh, train_w)
            t2 = simulate(margin, mh, test_w)
            p1, p2 = sum(x[0] for x in t1), sum(x[0] for x in t2)
            wr = (sum(x[0] > 0 for x in t2) / len(t2)) if t2 else 0
            print(f"  {margin:8.2f} {mh:8d} | {len(t1):12d} {p1:10.2f} | {len(t2):11d} {wr:6.1%} "
                  f"{p2:9.2f} {p2 / max(len(t2), 1):+7.3f}")
            rows_out.append(dict(margin=margin, max_hold=mh, train_trades=len(t1), train_pnl=p1,
                                 test_trades=len(t2), test_win_rate=wr, test_pnl=p2))
    print("\n  (train P&L is in-sample and flattering; only TEST counts)")
    report["scalp"] = rows_out
    Path("reports").mkdir(exist_ok=True)
    Path("reports/candles.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
