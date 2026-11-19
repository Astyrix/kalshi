"""Candles on any timeframe, named candlestick patterns, and multi-timeframe
features built from one continuous per-second index series."""
from __future__ import annotations

import math

import numpy as np

TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900}

# name -> expected direction (+1 bullish, -1 bearish, 0 neutral). The backtest
# measures what each one actually does; these signs are only the textbook view.
PATTERNS = {
    "doji": 0, "hammer": 1, "hanging_man": -1, "shooting_star": -1, "inverted_hammer": 1,
    "bull_engulfing": 1, "bear_engulfing": -1, "bull_harami": 1, "bear_harami": -1,
    "piercing": 1, "dark_cloud": -1, "morning_star": 1, "evening_star": -1,
    "three_soldiers": 1, "three_crows": -1, "bull_marubozu": 1, "bear_marubozu": -1,
    "inside_bar": 0, "outside_bar": 0, "tweezer_top": -1, "tweezer_bottom": 1,
}


class Series:
    """Per-second index values from stitched windows; NaN where missing."""

    def __init__(self, windows):
        pts = {}
        for w in windows:
            for t, v in w.index:
                pts[t] = v
        ts = np.array(sorted(pts), dtype=np.int64)
        self.t0 = int(ts[0])
        self.v = np.full(int(ts[-1]) - self.t0 + 1, np.nan)
        self.v[ts - self.t0] = [pts[t] for t in ts]
        # forward-fill short gaps (a second or two of missing prints)
        last = np.nan
        miss = 0
        for i in range(len(self.v)):
            if np.isnan(self.v[i]):
                miss += 1
                if miss <= 5:
                    self.v[i] = last
            else:
                last, miss = self.v[i], 0

    def upto(self, t: int, seconds: int) -> np.ndarray:
        """Values in (t - seconds, t]."""
        hi = t - self.t0 + 1
        lo = max(hi - seconds, 0)
        return self.v[lo:hi] if hi > 0 else np.array([])

    def at(self, t: int) -> float:
        i = t - self.t0
        return float(self.v[i]) if 0 <= i < len(self.v) else math.nan

    def candles(self, t: int, tf: int, n: int):
        """Last n *completed* tf-candles before t, plus the forming one.
        Candles align to clock boundaries (so 15m candles = Kalshi windows)."""
        cur0 = t - t % tf
        out = []
        for k in range(n, 0, -1):
            a = cur0 - k * tf
            seg = self.v[max(a - self.t0 + 1, 0): max(a + tf - self.t0 + 1, 0)]
            seg = seg[~np.isnan(seg)]
            out.append(None if len(seg) < tf * 0.5 else (seg[0], seg.max(), seg.min(), seg[-1]))
        seg = self.v[max(cur0 - self.t0 + 1, 0): t - self.t0 + 1]
        seg = seg[~np.isnan(seg)]
        forming = (seg[0], seg.max(), seg.min(), seg[-1]) if len(seg) else None
        return out, forming


def vol_per_sec(vals: np.ndarray) -> float:
    v = vals[~np.isnan(vals)]
    if len(v) < 60:
        return 2e-5
    p = v[::15]
    r = np.diff(np.log(p))
    return max(float(np.sqrt(np.mean(r * r) / 15)) if len(r) else 2e-5, 2e-6)


def detect(cs) -> dict[str, float]:
    """Named patterns on the last completed candles (cs[-1] is the newest)."""
    f = {p: 0.0 for p in PATTERNS}
    if len(cs) < 3 or any(c is None for c in cs[-3:]):
        return f
    (o2, h2, l2, c2), (o1, h1, l1, c1), (o0, h0, l0, c0) = cs[-3], cs[-2], cs[-1]
    rng = max(h0 - l0, 1e-9)
    body = abs(c0 - o0)
    up_w, lo_w = h0 - max(o0, c0), min(o0, c0) - l0
    prior = [c for c in cs[-6:-1] if c is not None]
    down_before = len(prior) >= 3 and prior[-1][3] < prior[0][0]
    up_before = len(prior) >= 3 and prior[-1][3] > prior[0][0]
    green0, red0, green1, red1 = c0 > o0, c0 < o0, c1 > o1, c1 < o1
    b1 = abs(c1 - o1)
    f["doji"] = float(body < 0.1 * rng)
    small_top = lo_w >= 2 * body and up_w <= max(body, 0.1 * rng)
    small_bot = up_w >= 2 * body and lo_w <= max(body, 0.1 * rng)
    f["hammer"] = float(small_top and down_before)
    f["hanging_man"] = float(small_top and up_before)
    f["shooting_star"] = float(small_bot and up_before)
    f["inverted_hammer"] = float(small_bot and down_before)
    f["bull_engulfing"] = float(red1 and green0 and c0 >= o1 and o0 <= c1 and body > b1)
    f["bear_engulfing"] = float(green1 and red0 and c0 <= o1 and o0 >= c1 and body > b1)
    inside_body = max(o0, c0) <= max(o1, c1) and min(o0, c0) >= min(o1, c1)
    f["bull_harami"] = float(red1 and green0 and inside_body and b1 > 2 * body)
    f["bear_harami"] = float(green1 and red0 and inside_body and b1 > 2 * body)
    f["piercing"] = float(red1 and green0 and o0 <= c1 and c0 > (o1 + c1) / 2 and c0 < o1)
    f["dark_cloud"] = float(green1 and red0 and o0 >= c1 and c0 < (o1 + c1) / 2 and c0 > o1)
    b2, r1 = abs(c2 - o2), max(h1 - l1, 1e-9)
    f["morning_star"] = float(c2 < o2 and b1 < 0.3 * b2 and green0 and c0 > (o2 + c2) / 2)
    f["evening_star"] = float(c2 > o2 and b1 < 0.3 * b2 and red0 and c0 < (o2 + c2) / 2)
    f["three_soldiers"] = float(c2 > o2 and green1 and green0 and c0 > c1 > c2)
    f["three_crows"] = float(c2 < o2 and red1 and red0 and c0 < c1 < c2)
    f["bull_marubozu"] = float(green0 and body >= 0.9 * rng)
    f["bear_marubozu"] = float(red0 and body >= 0.9 * rng)
    f["inside_bar"] = float(h0 <= h1 and l0 >= l1)
    f["outside_bar"] = float(h0 > h1 and l0 < l1)
    tol = 0.05 * max(rng, r1)
    f["tweezer_top"] = float(abs(h0 - h1) <= tol and green1 and red0)
    f["tweezer_bottom"] = float(abs(l0 - l1) <= tol and red1 and green0)
    return f


def _ema(x, n):
    k, e = 2 / (n + 1), x[0]
    for v in x[1:]:
        e += k * (v - e)
    return e


def tf_features(series: Series, t: int, name: str, tf: int, sig: float, price: float,
                n_shape: int = 4, indicators: bool = True) -> dict[str, float]:
    cs, forming = series.candles(t, tf, max(n_shape, 26 if indicators else 6))
    unit = max(sig * price * math.sqrt(tf), 1e-6)
    f = {}
    # raw shapes of the last n_shape completed candles: lets the model learn
    # patterns that have no textbook name
    for k in range(1, n_shape + 1):
        c = cs[-k] if len(cs) >= k else None
        o, h, l, cl = c if c is not None else (np.nan,) * 4
        f[f"{name}_c{k}_ret"] = (cl - o) / unit
        f[f"{name}_c{k}_upw"] = (h - max(o, cl)) / unit
        f[f"{name}_c{k}_low"] = (min(o, cl) - l) / unit
    if forming is not None:
        o, h, l, cl = forming
        f[f"{name}_now_ret"] = (cl - o) / unit
        f[f"{name}_now_upw"] = (h - max(o, cl)) / unit
        f[f"{name}_now_low"] = (min(o, cl) - l) / unit
        f[f"{name}_now_frac"] = (t % tf) / tf
    for p, v in detect(cs).items():
        f[f"{name}_pat_{p}"] = v
    closes = [c[3] for c in cs if c is not None] + ([forming[3]] if forming else [])
    if len(closes) >= 4:
        f[f"{name}_trend"] = float(np.sign(closes[-1] - closes[-4]))
        hi = max(c[1] for c in cs[-6:] if c is not None) if any(cs[-6:]) else closes[-1]
        lo = min(c[2] for c in cs[-6:] if c is not None) if any(cs[-6:]) else closes[-1]
        f[f"{name}_range_pos"] = (closes[-1] - lo) / max(hi - lo, 1e-9)
    else:
        f[f"{name}_trend"] = 0.0
        f[f"{name}_range_pos"] = 0.5
    if indicators and len(closes) >= 15:
        d = np.diff(closes[-15:])
        g, ls = d[d > 0].sum(), -d[d < 0].sum()
        f[f"{name}_rsi"] = (100 - 100 / (1 + g / ls) if ls > 0 else 100.0) / 100
        if len(closes) >= 26:
            f[f"{name}_macd"] = (_ema(closes, 12) - _ema(closes, 26)) / unit
            f[f"{name}_ema_gap"] = (_ema(closes, 9) - _ema(closes, 21)) / unit
    return f


def mtf_features(series: Series, t: int, indicators: bool = True) -> dict[str, float] | None:
    hist = series.upto(t, 1800)
    if len(hist) < 300 or np.isnan(hist[-1]):
        return None
    price = float(hist[-1])
    sig = vol_per_sec(hist)
    f = {"vol_bp": sig * 1e4}
    for name, tf in TIMEFRAMES.items():
        f.update(tf_features(series, t, name, tf, sig, price, indicators=indicators))
    trends = [f[f"{n}_trend"] for n in TIMEFRAMES]
    f["tf_agree"] = float(sum(trends))                 # -3..+3
    f["tf_all_up"] = float(all(x > 0 for x in trends))
    f["tf_all_down"] = float(all(x < 0 for x in trends))
    for s in (5, 15, 60):
        a = series.at(t - s)
        f[f"vel{s}"] = (price - a) / max(sig * price * math.sqrt(s), 1e-6) if not math.isnan(a) else 0.0
    return f
