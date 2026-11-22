"""Win-probability model.

Two layers:

1. A physics prior. Kalshi settles on the simple average of the 60 BRTI
   prints before close, compared with the strike. MaxyCharts' index already
   IS that 60 s average (its print at the close equals the settlement value
   in every cached window), so P(UP) = P(index now + remaining move > K).
   The remaining move comes from an empirical table of standardized final
   moves (fat tails, smoothing), fitted once on the oldest 60 days of
   history; volatility comes from 60 s changes of the averaged index.

2. A learned blend on top: logit(P) = a*logit(P0) + c*logit(market mid)
   + b + w . features, fitted online (walk-forward) from outcomes. The market
   price is usually the best single predictor, so the bot only finds an edge
   where the physics model or momentum disagrees with it and history says
   that disagreement pays. Momentum features such as RSI,
   MACD and the EMA 9/21 gap only change the probability if the data shows
   they actually help.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

BAR = 15                 # seconds per bar for the technical indicators
FEATURES = ["ema_gap", "macd_hist", "rsi", "velocity", "imbalance"]


def phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


# ---- settlement move ------------------------------------------------------
ZTAB = Path("state/brti-z.json")
LEFTS = (5, 10, 20, 30, 45, 60, 90, 120, 180, 300, 420, 600, 840)


def brti_vol(v) -> float:
    """BRTI's 1 s volatility in $ from the index, a 60 s rolling average:
    Var(A_t - A_t-60) = 40 sigma^2 (1 s changes of an average understate it ~8x).
    Uses up to the last 600 s."""
    v = np.asarray(v, dtype=float)[-601:]
    v = v[np.isfinite(v)]
    if len(v) < 120:
        return 2.0
    return float(max((v[60:] - v[:-60]).std() / math.sqrt(40), 0.5))


def move_scale(sig: float, left: float) -> float:
    """Scale of (index at close - index now): beyond 60 s the average is all future
    prints (variance ~ sigma^2 (left - 40)); inside the last minute part of it is known."""
    return max(sig * math.sqrt(max(left - 40 if left >= 60 else left ** 3 / 10800, 1)), 1.0)


def fit_z(days: float = 60, path: Path = ZTAB) -> dict:
    """Standardized final moves (settlement - index now) / move_scale for each time left,
    from the OLDEST `days` of cached windows (before any walk-forward test month)."""
    from .sweep import load_cached
    ws = [w for w in load_cached("BTC", 3650) if w.strike and len(w.index) > 900]
    if not ws:
        raise RuntimeError("no cached windows to fit the settlement-move table")
    ws = [w for w in ws if w.close_ts < ws[0].close_ts + days * 86400]
    out = {}
    for L in LEFTS:
        z = []
        for w in ws:
            idx = np.asarray(w.index, dtype=float)
            fin = idx[idx[:, 0] <= w.close_ts]
            j = np.searchsorted(idx[:, 0], w.close_ts - L, side="right") - 1
            if j < 300 or not len(fin) or fin[-1, 0] < w.close_ts - 2:
                continue
            z.append((fin[-1, 1] - idx[j, 1]) / move_scale(brti_vol(idx[:j + 1, 1]), L))
        out[L] = sorted(round(float(x), 4) for x in z)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"fitted_on": [ws[0].ticker, ws[-1].ticker], "z": out}))
    return out


_Z: dict = {}


def ztab() -> dict:
    if not _Z:
        try:
            _Z.update({int(k): np.array(v) for k, v in json.loads(ZTAB.read_text())["z"].items()})
        except Exception:
            _Z.update({int(k): np.array(v) for k, v in fit_z().items()})
    return _Z


def final_moves(sig: float, left: float) -> np.ndarray:
    """Samples of (index at the close - index now) for this volatility and time left."""
    Z = ztab()
    L = min(Z, key=lambda k: abs(math.log(max(left, 1) / k)))
    return Z[L] * move_scale(sig, left)


def p_up(dist: float, sig: float, left: float) -> float:
    """P(settlement > strike) with the index `dist` above the strike now."""
    if left <= 0:
        return 1.0 if dist > 0 else 0.0
    m = final_moves(sig, left)
    p = float(np.mean(dist + m > 0))
    return min(max(p, 0.5 / len(m)), 1 - 0.5 / len(m))


# ---- indicators -----------------------------------------------------------
def ema(xs: list[float], n: int) -> list[float]:
    if not xs:
        return []
    k, out = 2 / (n + 1), [xs[0]]
    for x in xs[1:]:
        out.append(out[-1] + k * (x - out[-1]))
    return out


def rsi(xs: list[float], n: int = 14) -> float:
    if len(xs) < n + 1:
        return 50.0
    gains = losses = 0.0
    for a, b in zip(xs[-n - 1:-1], xs[-n:]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def bars(index: list[tuple[int, float]], width: int = BAR) -> list[float]:
    """Close of each `width`-second bar."""
    out, cur = [], None
    for t, v in index:
        b = t // width
        if b != cur:
            out.append(v)
            cur = b
        else:
            out[-1] = v
    return out


def realized_vol(index: list[tuple[int, float]], lookback: int = 300) -> float:
    """Per-second stdev of log returns over the last `lookback` seconds."""
    pts = index[-lookback - 1:]
    rets = [math.log(b[1] / a[1]) for a, b in zip(pts, pts[1:]) if a[1] > 0 and b[1] > 0]
    if len(rets) < 20:
        return 2e-5
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    # BRTI is smoothed, so raw 1-second returns understate the risk. Use the
    # larger of the 1-second and the 15-second measure (scaled per second).
    p15 = pts[::15]
    r15 = [math.log(b[1] / a[1]) for a, b in zip(p15, p15[1:]) if a[1] > 0 and b[1] > 0]
    var15 = (sum(r * r for r in r15) / len(r15) / 15) if len(r15) >= 5 else 0.0
    return max(math.sqrt(max(var, var15)), 2e-6)


@dataclass
class Snapshot:
    """Everything the model knows at one moment."""
    t: int
    price: float
    strike: float
    close_ts: int
    sigma: float                 # per-second log-vol
    p0: float                    # physics prior P(UP)
    features: dict[str, float]
    settle_sd: float             # sd of the settlement average, in index points
    mid: float | None = None     # Kalshi YES mid price, if known
    brti_sigma: float = 2.0      # BRTI 1 s volatility in $ (see brti_vol)

    @property
    def tau(self) -> int:
        return max(self.close_ts - self.t, 0)


def snapshot(index: list[tuple[int, float]], strike: float, close_ts: int, t: int,
             imbalance: float = 0.0, mid: float | None = None) -> Snapshot | None:
    pts = [p for p in index if p[0] <= t]
    if len(pts) < 30:
        return None
    x = pts[-1][1]
    sig = realized_vol(pts)
    tau = max(close_ts - t, 0)
    # The index is already the settlement average, so settlement = index at the close.
    sb = brti_vol([v for _, v in pts])
    p0 = p_up(x - strike, sb, tau)
    sd_avg = float(final_moves(sb, tau).std()) if tau > 0 else 0.0

    closes = bars(pts)
    e9, e21 = ema(closes, 9), ema(closes, 21)
    m12, m26 = ema(closes, 12), ema(closes, 26)
    macd = [a - b for a, b in zip(m12, m26)]
    sig9 = ema(macd, 9)
    unit = max(sig * x * math.sqrt(BAR), 1e-9)          # one bar's typical move
    v60 = pts[-1][1] - pts[max(len(pts) - 61, 0)][1]
    feats = {
        "ema_gap": (e9[-1] - e21[-1]) / unit,
        "macd_hist": (macd[-1] - sig9[-1]) / unit,
        "rsi": (rsi(closes) - 50) / 50,
        "velocity": v60 / max(sig * x * math.sqrt(60), 1e-9),
        "imbalance": imbalance,
    }
    feats = {k: max(min(v, 5.0), -5.0) for k, v in feats.items()}
    return Snapshot(t=t, price=x, strike=strike, close_ts=close_ts, sigma=sig,
                    p0=p0, features=feats, settle_sd=sd_avg, mid=mid, brti_sigma=sb)


def price_after_move(s: Snapshot, new_price: float, dt: int) -> float:
    """Physics prior P(UP) if the index moved to `new_price` over the next dt seconds."""
    return p_up(new_price - s.strike, s.brti_sigma, max(s.tau - dt, 0))


# ---- learned calibration ----------------------------------------------------
@dataclass
class Calibrator:
    a: float = 0.5               # weight on the physics prior
    c: float = 0.5               # weight on the market's own price
    b: float = 0.0
    w: dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in FEATURES})
    lr: float = 0.01
    l2: float = 1e-3
    n: int = 0

    def p_up(self, s: Snapshot) -> float:
        if s.p0 in (0.0, 1.0):
            return s.p0
        lm = logit(s.mid) if s.mid is not None else logit(s.p0)
        z = self.a * logit(s.p0) + self.c * lm + self.b + sum(self.w[k] * s.features.get(k, 0.0) for k in FEATURES)
        return sigmoid(z)

    def update(self, s: Snapshot, outcome_up: bool):
        """One SGD step on log-loss."""
        if s.p0 in (0.0, 1.0):
            return
        err = self.p_up(s) - (1.0 if outcome_up else 0.0)
        lp = logit(s.p0)
        lm = logit(s.mid) if s.mid is not None else lp
        self.a -= self.lr * (err * lp + self.l2 * (self.a - 0.5))
        self.c -= self.lr * (err * lm + self.l2 * (self.c - 0.5))
        self.b -= self.lr * (err + self.l2 * self.b)
        for k in FEATURES:
            self.w[k] -= self.lr * (err * s.features.get(k, 0.0) + self.l2 * self.w[k])
        self.n += 1

    def save(self, path: Path):
        path.write_text(json.dumps(self.__dict__, indent=2))

    @classmethod
    def load(cls, path: Path) -> "Calibrator":
        if not path.exists():
            return cls()
        return cls(**json.loads(path.read_text()))
