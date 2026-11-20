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
    "spinning_top": 0, "dragonfly_doji": 1, "gravestone_doji": -1, "long_legged_doji": 0,
    "three_inside_up": 1, "three_inside_down": -1, "three_outside_up": 1, "three_outside_down": -1,
    "bull_kicker": 1, "bear_kicker": -1, "bull_belt_hold": 1, "bear_belt_hold": -1,
    "rising_three": 1, "falling_three": -1,
}

# chart patterns over the last ~30 candles of one timeframe
CHART_PATTERNS = {
    "double_top": -1, "double_bottom": 1, "head_shoulders": -1, "inv_head_shoulders": 1,
    "breakout_up": 1, "breakout_down": -1, "failed_breakout_up": -1, "failed_breakout_down": 1,
    "higher_highs_lows": 1, "lower_highs_lows": -1,
    "asc_triangle": 1, "desc_triangle": -1, "sym_triangle": 0,
    "rising_wedge": -1, "falling_wedge": 1, "bull_flag": 1, "bear_flag": -1,
    "nr7_squeeze": 0, "range_top_reject": -1, "range_bottom_bounce": 1,
}


class Series:
    """Per-second index values from stitched windows; NaN where missing."""

    def __init__(self, windows):
        arrs = [np.asarray(w.index, dtype=float).reshape(-1, 2) for w in windows if len(w.index)]
        allp = np.concatenate(arrs) if arrs else np.zeros((1, 2))
        ts = allp[:, 0].astype(np.int64)
        self.t0 = int(ts.min())
        self.v = np.full(int(ts.max()) - self.t0 + 1, np.nan)
        self.v[ts - self.t0] = allp[:, 1]          # later windows overwrite overlaps
        self._ffill(0)

    def _ffill(self, lo, limit=5):
        """Forward-fill gaps of up to `limit` seconds (a missing print or two)."""
        v = self.v[lo:]
        ok = ~np.isnan(v)
        if ok.all() or not ok.any():
            return
        idx = np.where(ok, np.arange(len(v)), -1)
        last = np.maximum.accumulate(idx)                  # index of last valid value
        gap = np.arange(len(v)) - last
        fill = (~ok) & (last >= 0) & (gap <= limit)
        v[fill] = v[last[fill]]

    def extend(self, points):
        """Add live (ts, value) points, growing the array as needed."""
        if not points:
            return
        end = max(int(t) for t, _ in points)
        old_len = len(self.v)
        if end - self.t0 + 1 > old_len:
            self.v = np.concatenate([self.v, np.full(end - self.t0 + 1 - old_len, np.nan)])
        for t, v in points:
            i = int(t) - self.t0
            if i >= 0:
                self.v[i] = v
        # forward-fill short gaps in the newly touched tail
        self._ffill(max(min(int(t) for t, _ in points) - self.t0 - 6, 0))

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
    # --- more single / multi-candle patterns
    f["spinning_top"] = float(0.1 * rng <= body <= 0.3 * rng and up_w > body and lo_w > body)
    doji = body < 0.1 * rng
    f["dragonfly_doji"] = float(doji and up_w < 0.1 * rng and lo_w > 0.6 * rng)
    f["gravestone_doji"] = float(doji and lo_w < 0.1 * rng and up_w > 0.6 * rng)
    f["long_legged_doji"] = float(doji and up_w > 0.35 * rng and lo_w > 0.35 * rng)
    b2_in = max(o1, c1) <= max(o2, c2) and min(o1, c1) >= min(o2, c2)
    f["three_inside_up"] = float(c2 < o2 and green1 and b2_in and green0 and c0 > o2)
    f["three_inside_down"] = float(c2 > o2 and red1 and b2_in and red0 and c0 < o2)
    eng_up = c2 < o2 and green1 and c1 >= o2 and o1 <= c2 and b1 > b2
    eng_dn = c2 > o2 and red1 and c1 <= o2 and o1 >= c2 and b1 > b2
    f["three_outside_up"] = float(eng_up and green0 and c0 > c1)
    f["three_outside_down"] = float(eng_dn and red0 and c0 < c1)
    big0 = body >= 0.6 * rng
    f["bull_kicker"] = float(red1 and green0 and big0 and o0 >= o1 and l0 >= c1)
    f["bear_kicker"] = float(green1 and red0 and big0 and o0 <= o1 and h0 <= c1)
    f["bull_belt_hold"] = float(green0 and lo_w <= 0.05 * rng and body >= 0.7 * rng and down_before)
    f["bear_belt_hold"] = float(red0 and up_w <= 0.05 * rng and body >= 0.7 * rng and up_before)
    if len(cs) >= 5 and all(c is not None for c in cs[-5:]):
        a, m1, m2, m3, z = cs[-5:]
        inside = all(c[1] <= a[1] and c[2] >= a[2] for c in (m1, m2, m3))
        ab = abs(a[3] - a[0])
        f["rising_three"] = float(a[3] > a[0] and ab > 0 and inside and z[3] > z[0] and z[3] > a[3])
        f["falling_three"] = float(a[3] < a[0] and ab > 0 and inside and z[3] < z[0] and z[3] < a[3])
    else:
        f["rising_three"] = f["falling_three"] = 0.0
    return f


def _pivots(vals, k=2, kind="high"):
    """Indices of swing highs (or lows): extreme of the 2k+1 bars around them."""
    out = []
    for i in range(k, len(vals) - k):
        seg = vals[i - k:i + k + 1]
        if (kind == "high" and vals[i] == max(seg)) or (kind == "low" and vals[i] == min(seg)):
            out.append(i)
    return out


def _slope(idx, vals):
    if len(idx) < 2:
        return 0.0
    return float(np.polyfit(idx, vals, 1)[0])


def chart_patterns(cs, unit: float) -> dict[str, float]:
    """Chart patterns on completed candles (cs[-1] newest). `unit` is a typical
    candle move in dollars so tolerances scale with volatility."""
    f = {p: 0.0 for p in CHART_PATTERNS}
    cs = [c for c in cs[-30:] if c is not None]
    if len(cs) < 21:
        return f
    H = np.array([c[1] for c in cs]); L = np.array([c[2] for c in cs])
    C = np.array([c[3] for c in cs]); O = np.array([c[0] for c in cs])
    rng = max(H.max() - L.min(), 1e-9)
    tol = max(0.15 * unit, 0.04 * rng)
    x = C[-1]
    ph, pl = _pivots(H[:-1], 2, "high"), _pivots(L[:-1], 2, "low")
    # double top / bottom: two similar swing extremes, price now back through the middle
    if len(ph) >= 2:
        a, b = ph[-2], ph[-1]
        mid_low = L[a:b + 1].min()
        if b - a >= 3 and abs(H[a] - H[b]) <= tol and max(H[a], H[b]) - mid_low > 2 * tol:
            f["double_top"] = float(x < (H[b] + mid_low) / 2)
    if len(pl) >= 2:
        a, b = pl[-2], pl[-1]
        mid_high = H[a:b + 1].max()
        if b - a >= 3 and abs(L[a] - L[b]) <= tol and mid_high - min(L[a], L[b]) > 2 * tol:
            f["double_bottom"] = float(x > (L[b] + mid_high) / 2)
    # head and shoulders: three swing highs, middle highest, shoulders level
    if len(ph) >= 3:
        l_, h_, r_ = ph[-3:]
        if H[h_] > max(H[l_], H[r_]) + tol and abs(H[l_] - H[r_]) <= 2 * tol:
            neck = min(L[l_:r_ + 1])
            f["head_shoulders"] = float(x < (H[r_] + neck) / 2)
    if len(pl) >= 3:
        l_, h_, r_ = pl[-3:]
        if L[h_] < min(L[l_], L[r_]) - tol and abs(L[l_] - L[r_]) <= 2 * tol:
            neck = max(H[l_:r_ + 1])
            f["inv_head_shoulders"] = float(x > (L[r_] + neck) / 2)
    # breakouts of the prior 20-candle range, and failed breakouts
    hi20, lo20 = H[-21:-1].max(), L[-21:-1].min()
    f["breakout_up"] = float(C[-1] > hi20)
    f["breakout_down"] = float(C[-1] < lo20)
    f["failed_breakout_up"] = float(H[-1] > hi20 and C[-1] < hi20)
    f["failed_breakout_down"] = float(L[-1] < lo20 and C[-1] > lo20)
    # market structure from the last three swings
    if len(ph) >= 2 and len(pl) >= 2:
        f["higher_highs_lows"] = float(H[ph[-1]] > H[ph[-2]] and L[pl[-1]] > L[pl[-2]])
        f["lower_highs_lows"] = float(H[ph[-1]] < H[ph[-2]] and L[pl[-1]] < L[pl[-2]])
    # triangles and wedges from the slope of swing highs vs swing lows (last 20)
    ph20 = [i for i in ph if i >= len(cs) - 21]
    pl20 = [i for i in pl if i >= len(cs) - 21]
    if len(ph20) >= 2 and len(pl20) >= 2:
        sh = _slope(ph20, H[ph20]) / unit
        sl = _slope(pl20, L[pl20]) / unit
        flat = 0.03
        converging = sh < sl - flat
        f["asc_triangle"] = float(abs(sh) <= flat and sl > flat)
        f["desc_triangle"] = float(abs(sl) <= flat and sh < -flat)
        f["sym_triangle"] = float(sh < -flat and sl > flat)
        f["rising_wedge"] = float(sh > flat and sl > flat and converging)
        f["falling_wedge"] = float(sh < -flat and sl < -flat and converging)
    # flags: strong impulse, then a small counter-drift that holds most of it
    imp = C[-9] - O[-14]
    pull = C[-1] - C[-9]
    band = H[-8:].max() - L[-8:].min()
    if imp > 3 * unit and -0.5 * imp < pull <= 0 and band < 0.6 * imp:
        f["bull_flag"] = 1.0
    if imp < -3 * unit and 0 <= pull < -0.5 * imp and band < -0.6 * imp:
        f["bear_flag"] = 1.0
    # NR7: narrowest range of the last 7 candles (volatility squeeze)
    r7 = H[-7:] - L[-7:]
    f["nr7_squeeze"] = float(r7[-1] == r7.min())
    # sideways range: touch of the top / bottom that closes back inside
    eff = abs(C[-1] - C[-20]) / max(np.abs(np.diff(C[-20:])).sum(), 1e-9)
    if eff < 0.25:
        top, bot = H[-20:-1].max(), L[-20:-1].min()
        f["range_top_reject"] = float(H[-1] >= top - tol and C[-1] < O[-1] and C[-1] < top - tol)
        f["range_bottom_bounce"] = float(L[-1] <= bot + tol and C[-1] > O[-1] and C[-1] > bot + tol)
    return f


def _ema(x, n):
    k, e = 2 / (n + 1), x[0]
    for v in x[1:]:
        e += k * (v - e)
    return e


def tf_features(series: Series, t: int, name: str, tf: int, sig: float, price: float,
                n_shape: int = 4, indicators: bool = True, chart: bool = True) -> dict[str, float]:
    cs, forming = series.candles(t, tf, max(n_shape, 30))
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
    if chart:
        for p, v in chart_patterns(cs, unit).items():
            f[f"{name}_chart_{p}"] = v
    closes = [c[3] for c in cs if c is not None] + ([forming[3]] if forming else [])
    if len(closes) >= 4:
        f[f"{name}_trend"] = float(np.sign(closes[-1] - closes[-4]))
        hi = max((c[1] for c in cs[-6:] if c is not None), default=closes[-1])
        lo = min((c[2] for c in cs[-6:] if c is not None), default=closes[-1])
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


def mtf_features(series: Series, t: int, indicators: bool = True, chart: bool = True) -> dict[str, float] | None:
    hist = series.upto(t, 1800)
    if len(hist) < 300 or np.isnan(hist[-1]):
        return None
    price = float(hist[-1])
    sig = vol_per_sec(hist)
    f = {"vol_bp": sig * 1e4}
    for name, tf in TIMEFRAMES.items():
        f.update(tf_features(series, t, name, tf, sig, price, indicators=indicators, chart=chart))
    trends = [f[f"{n}_trend"] for n in TIMEFRAMES]
    f["tf_agree"] = float(sum(trends))                 # -3..+3
    f["tf_all_up"] = float(all(x > 0 for x in trends))
    f["tf_all_down"] = float(all(x < 0 for x in trends))
    for s in (5, 15, 60):
        a = series.at(t - s)
        f[f"vel{s}"] = (price - a) / max(sig * price * math.sqrt(s), 1e-6) if not math.isnan(a) else 0.0
    return f
