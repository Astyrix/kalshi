"""Win-probability model.

Two layers:

1. A physics prior. Kalshi settles on the simple average of the 60 BRTI
   prints before close, compared with the strike. Treating the index as a
   random walk with volatility measured from recent seconds, that average is
   approximately normal, which gives P(UP) = Phi((E[avg] - K) / sd[avg]).
   The part of the averaging window that has already printed is known exactly.

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
    # Settlement = mean of the 60 prints in (close-60, close].
    known = [v for ts, v in pts if close_ts - 60 < ts <= close_ts]
    n_future = max(60 - len(known), 0) if tau < 60 else 60
    n_future = min(n_future, 60)
    gap = max(tau - 60, 0)
    mean_avg = (sum(known) + n_future * x) / 60
    sd_avg = (n_future / 60) * sig * x * math.sqrt(gap + n_future / 3) if n_future else 0.0
    if sd_avg <= 1e-9:
        p0 = 1.0 if mean_avg >= strike else 0.0
    else:
        p0 = phi((mean_avg - strike) / sd_avg)

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
                    p0=p0, features=feats, settle_sd=sd_avg, mid=mid)


def price_after_move(s: Snapshot, new_price: float, dt: int) -> float:
    """Physics prior P(UP) if the index moved to `new_price` over the next dt seconds."""
    tau = max(s.tau - dt, 0)
    gap, n = max(tau - 60, 0), (60 if tau >= 60 else max(tau, 0))
    if n == 0:
        return 1.0 if new_price >= s.strike else 0.0
    sd = (n / 60) * s.sigma * new_price * math.sqrt(gap + n / 3)
    return phi((new_price - s.strike) / max(sd, 1e-9))


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
