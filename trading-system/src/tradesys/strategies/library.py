"""Baseline strategy families, each with exact, parameterised rules.

None of these is assumed to work. Each exists to be tested: in-sample on 2010-2019 with
walk-forward splits, then frozen and run once on 2020 onward. All read only the causal
feature row of the bar that just closed (see feature_engineering.features), and every
signal carries a stop. Prices are SPY dollars; `R` is the distance from the signal price
to the stop.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

import pandas as pd

from .base import Position, Signal


def _ok(*xs: Any) -> bool:
    return all(x is not None and isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


class _Base:
    name = "base"

    def __init__(self, **params: Any):
        self.params = {**self.defaults(), **params}
        unknown = set(params) - set(self.defaults())
        if unknown:
            raise ValueError(f"{self.name}: unknown parameters {sorted(unknown)}")
        self.ctx: pd.Series | None = None
        self.entries = 0

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {}

    def on_session_start(self, day: dt.date, ctx: pd.Series) -> None:
        self.ctx, self.entries = ctx, 0

    def _target(self, side: int, price: float, stop: float, r: float | None) -> float | None:
        return None if r is None else price + side * r * abs(price - stop)


class OpeningRangeBreakout(_Base):
    """Close beyond the N-minute opening range -> trade in the breakout's direction.

    stop: 'mid' (range midpoint) or 'far' (the other side of the range).
    min_relvol: the breakout bar's volume vs that minute's 20-day average (0 = off).
    max_range_atr: skip days whose range is wider than this many daily ATRs (0 = off)."""
    name = "orb"

    @classmethod
    def defaults(cls):
        return {"minutes": 15, "stop": "mid", "target_r": 2.0, "sides": "both", "min_relvol": 0.0,
                "max_range_atr": 0.0, "max_entries": 1, "max_hold_min": None}

    def on_bar_close(self, row: pd.Series, position: Position | None):
        p = self.params
        if position is not None or self.entries >= p["max_entries"]:
            return None
        hi, lo, c = row.get(f"or{p['minutes']}_high"), row.get(f"or{p['minutes']}_low"), row["close"]
        if not _ok(hi, lo) or row["minute"] < p["minutes"]:
            return None
        if p["max_range_atr"] and _ok(self.ctx.get("atr_d14")) and (hi - lo) > p["max_range_atr"] * self.ctx["atr_d14"]:
            return None
        if p["min_relvol"] and not (_ok(row.get("relvol")) and row["relvol"] >= p["min_relvol"]):
            return None
        side = 1 if c > hi else -1 if c < lo else 0
        if side == 0 or (p["sides"] == "long" and side < 0) or (p["sides"] == "short" and side > 0):
            return None
        stop = (hi + lo) / 2 if p["stop"] == "mid" else (lo if side > 0 else hi)
        self.entries += 1
        return Signal(side, stop, self._target(side, c, stop, p["target_r"]), p["max_hold_min"],
                      reason=f"{p['minutes']}-min range {'high' if side > 0 else 'low'} broken",
                      evidence={"or_high": hi, "or_low": lo, "relvol": row.get("relvol")})


class VWAPTrend(_Base):
    """Trend continuation: EMA9 vs EMA20 sets the trend; a pullback that tags VWAP (within
    touch_atr ATRs) and closes back on the trend side enters; stop beyond VWAP by stop_atr ATRs."""
    name = "vwap_trend"

    @classmethod
    def defaults(cls):
        return {"start_minute": 15, "touch_atr": 0.25, "stop_atr": 1.0, "target_r": 2.0, "max_entries": 2,
                "max_hold_min": 45}

    def on_bar_close(self, row, position):
        p = self.params
        if position is not None or self.entries >= p["max_entries"] or row["minute"] < p["start_minute"]:
            return None
        vw, a, c = row.get("vwap"), row.get("atr14"), row["close"]
        if not _ok(vw, a, row.get("ema9"), row.get("ema20")) or a <= 0:
            return None
        up, dn = row["ema9"] > row["ema20"], row["ema9"] < row["ema20"]
        if up and row["low"] <= vw + p["touch_atr"] * a and c > vw:
            side, stop = 1, vw - p["stop_atr"] * a
        elif dn and row["high"] >= vw - p["touch_atr"] * a and c < vw:
            side, stop = -1, vw + p["stop_atr"] * a
        else:
            return None
        self.entries += 1
        return Signal(side, stop, self._target(side, c, stop, p["target_r"]), p["max_hold_min"],
                      reason="pullback to VWAP in an EMA9/20 trend", evidence={"vwap": vw, "atr": a})


class VWAPReversion(_Base):
    """Mean reversion: price stretched more than stretch_atr ATRs from VWAP with RSI beyond
    rsi_extreme -> fade toward VWAP (target = VWAP); stop stop_atr ATRs beyond the entry."""
    name = "vwap_reversion"

    @classmethod
    def defaults(cls):
        return {"start_minute": 10, "stretch_atr": 3.0, "rsi_extreme": 25.0, "stop_atr": 1.5, "max_entries": 2,
                "max_hold_min": 30, "entry": "market", "limit_expiry": 5}

    def on_bar_close(self, row, position):
        p = self.params
        if position is not None or self.entries >= p["max_entries"] or row["minute"] < p["start_minute"]:
            return None
        vw, a, c, r = row.get("vwap"), row.get("atr14"), row["close"], row.get("rsi14")
        if not _ok(vw, a, r) or a <= 0:
            return None
        if c < vw - p["stretch_atr"] * a and r < p["rsi_extreme"]:
            side = 1
        elif c > vw + p["stretch_atr"] * a and r > 100 - p["rsi_extreme"]:
            side = -1
        else:
            return None
        stop = c - side * p["stop_atr"] * a
        self.entries += 1
        lim = c if p["entry"] == "limit" else None
        return Signal(side, stop, vw, p["max_hold_min"], limit=lim, limit_expiry_min=p["limit_expiry"],
                      reason="stretched from VWAP, RSI extreme",
                      evidence={"vwap": vw, "atr": a, "rsi": r, "stretch_atr": (c - vw) / a})


class GapFill(_Base):
    """Fade an opening gap of min_gap..max_gap toward the prior close (target = prior close),
    entering at the close of the entry_minute bar if the gap is still open; stop = the session
    extreme on the gap side plus buffer_atr ATRs."""
    name = "gap_fill"

    @classmethod
    def defaults(cls):
        return {"min_gap": 0.0015, "max_gap": 0.01, "entry_minute": 4, "buffer_atr": 0.5, "max_hold_min": 60,
                "entry": "market", "limit_expiry": 5}

    def on_bar_close(self, row, position):
        p = self.params
        if position is not None or self.entries or row["minute"] != p["entry_minute"]:
            return None
        g, pc, a, c = self.ctx.get("gap_adj"), self.ctx.get("prev_close"), row.get("atr14"), row["close"]
        if not _ok(g, pc, a) or not (p["min_gap"] <= abs(g) <= p["max_gap"]):
            return None
        side = -1 if g > 0 else 1
        if (side < 0 and c <= pc) or (side > 0 and c >= pc):
            return None                                           # already filled
        stop = row["sess_high"] + p["buffer_atr"] * a if side < 0 else row["sess_low"] - p["buffer_atr"] * a
        self.entries += 1
        lim = c if p["entry"] == "limit" else None                # join at the signal bar's close
        return Signal(side, stop, pc, p["max_hold_min"], limit=lim, limit_expiry_min=p["limit_expiry"],
                      reason=f"fade a {g:+.2%} gap to the prior close", evidence={"gap": g, "prev_close": pc})


class MomentumBreakout(_Base):
    """A close at a new session high (low) after start_minute, on relative volume >= min_relvol
    and ROC15 in the same direction -> follow it; stop stop_atr ATRs back."""
    name = "momentum"

    @classmethod
    def defaults(cls):
        return {"start_minute": 30, "min_relvol": 1.5, "stop_atr": 2.0, "target_r": 2.0, "max_entries": 1,
                "max_hold_min": 40}

    def __init__(self, **params):
        super().__init__(**params)
        self._prev_hi = self._prev_lo = None

    def on_session_start(self, day, ctx):
        super().on_session_start(day, ctx)
        self._prev_hi = self._prev_lo = None

    def on_bar_close(self, row, position):
        p = self.params
        prev_hi, prev_lo = self._prev_hi, self._prev_lo
        self._prev_hi, self._prev_lo = row["sess_high"], row["sess_low"]
        if position is not None or self.entries >= p["max_entries"] or row["minute"] < p["start_minute"]:
            return None
        a, rv, roc, c = row.get("atr14"), row.get("relvol"), row.get("roc15"), row["close"]
        if not _ok(a, rv, roc, prev_hi, prev_lo) or a <= 0 or rv < p["min_relvol"]:
            return None
        if c > prev_hi and roc > 0:
            side = 1
        elif c < prev_lo and roc < 0:
            side = -1
        else:
            return None
        stop = c - side * p["stop_atr"] * a
        self.entries += 1
        return Signal(side, stop, self._target(side, c, stop, p["target_r"]), p["max_hold_min"],
                      reason="new session extreme on heavy volume", evidence={"relvol": rv, "roc15": roc})


class LiquiditySweep(_Base):
    """Sweep and reclaim: the bar trades beyond the prior-day or pre-market extreme and closes
    back inside -> trade back in; stop beyond the sweep bar's extreme plus buffer_atr ATRs."""
    name = "sweep"

    @classmethod
    def defaults(cls):
        return {"levels": ("prev", "pm"), "buffer_atr": 0.25, "target_r": 2.0, "max_entries": 1, "max_hold_min": 45}

    def on_bar_close(self, row, position):
        p = self.params
        if position is not None or self.entries >= p["max_entries"]:
            return None
        a, c = row.get("atr14"), row["close"]
        if not _ok(a) or a <= 0:
            return None
        for lv in p["levels"]:
            lo, hi = self.ctx.get(f"{lv}_low"), self.ctx.get(f"{lv}_high")
            if _ok(lo) and row["low"] < lo < c:
                stop = row["low"] - p["buffer_atr"] * a
                self.entries += 1
                return Signal(1, stop, self._target(1, c, stop, p["target_r"]), p["max_hold_min"],
                              reason=f"swept the {lv} low and reclaimed it", evidence={"level": lo})
            if _ok(hi) and row["high"] > hi > c:
                stop = row["high"] + p["buffer_atr"] * a
                self.entries += 1
                return Signal(-1, stop, self._target(-1, c, stop, p["target_r"]), p["max_hold_min"],
                              reason=f"swept the {lv} high and lost it", evidence={"level": hi})
        return None


class OpenDrive(_Base):
    """Opening drive (from the in-sample time-of-day study: 5-minute returns are positively
    autocorrelated only in 9:30-9:45). At the close of bar `minute`, if price has moved more
    than k daily ATRs from the session open, follow it; stop at the session open (the drive
    failed if price returns there); exit after max_hold_min minutes."""
    name = "open_drive"

    @classmethod
    def defaults(cls):
        return {"minute": 4, "k": 0.1, "max_hold_min": 10}

    def on_bar_close(self, row, position):
        p = self.params
        if position is not None or self.entries or row["minute"] != p["minute"]:
            return None
        o, c, atr = row.get("sess_open"), row["close"], self.ctx.get("atr_d14")
        if not _ok(o, atr) or atr <= 0 or abs(c - o) < p["k"] * atr:
            return None
        side = 1 if c > o else -1
        self.entries += 1
        return Signal(side, o, None, p["max_hold_min"], reason=f"opening drive {c - o:+.2f} from the open",
                      evidence={"move": c - o, "atr_d14": atr})


REGISTRY = {c.name: c for c in (OpeningRangeBreakout, VWAPTrend, VWAPReversion, GapFill, MomentumBreakout,
                                LiquiditySweep, OpenDrive)}


def make(name: str, **params: Any):
    return REGISTRY[name](**params)
