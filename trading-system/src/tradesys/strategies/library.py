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



# ---------------------------------------------------------------- published / famous-trader rules
# Each adapted to the 9:30-11:00 window (entries until 10:50, flat by 10:55). Sources are
# secondary summaries where the original text was not accessible; assumptions are stated.

class FirstCandle(_Base):
    """Zarattini & Aziz (2023) ORB variant: trade the direction of the first N-minute candle
    (close above its open: long; below: short; a doji: no trade) at the next bar's open; stop at
    the candle's other extreme; target R multiple (the paper used 10R, i.e. usually the time exit)."""
    name = "first_candle"

    @classmethod
    def defaults(cls):
        return {"candle_min": 5, "target_r": None}

    def on_bar_close(self, row, position):
        p = self.params
        if position is not None or self.entries or row["minute"] != p["candle_min"] - 1:
            return None
        o, c, hi, lo = row.get("sess_open"), row["close"], row["sess_high"], row["sess_low"]
        if not _ok(o, hi, lo) or abs(c - o) < 0.01:
            return None
        side = 1 if c > o else -1
        stop = lo if side > 0 else hi
        if (c - stop) * side <= 0.01:
            return None
        self.entries += 1
        return Signal(side, stop, self._target(side, c, stop, p["target_r"]), None,
                      reason=f"first {p['candle_min']}-minute candle {'up' if side > 0 else 'down'}",
                      evidence={"candle_open": o, "candle_close": c})


class NoiseMomentum(_Base):
    """Zarattini, Aziz & Barbon (2024) 'noise area' momentum, per secondary summaries: bands
    UB = max(open, prev close) * (1 + mult * sigma), LB = min(open, prev close) * (1 - mult * sigma),
    sigma = mean |move from the open| at this minute over the previous 14 sessions. At check times
    (every `every` minutes from `first_check`), a close above UB goes long, below LB short; an open
    position is closed at a check if price is back inside max(UB, VWAP) (long) / min(LB, VWAP) (short).
    Hard stop at the opposite band. ASSUMPTION: checks use the close of the bar ending at the check time."""
    name = "noise_momentum"

    @classmethod
    def defaults(cls):
        return {"mult": 1.0, "every": 30, "first_check": 30}

    def _bands(self, row):
        sg, o, pc = row.get("noise_sigma"), row.get("sess_open"), self.ctx.get("prev_close")
        if not _ok(sg, o, pc):
            return None
        return max(o, pc) * (1 + self.params["mult"] * sg), min(o, pc) * (1 - self.params["mult"] * sg)

    def on_bar_close(self, row, position):
        p = self.params
        m = row["minute"] + 1                                    # minutes elapsed at this bar's close
        if m < p["first_check"] or (m - p["first_check"]) % p["every"]:
            return None
        b = self._bands(row)
        if b is None:
            return None
        ub, lb = b
        c, vw = row["close"], row.get("vwap")
        if position is not None:
            if position.side > 0 and c < max(ub, vw if _ok(vw) else ub):
                return "exit"
            if position.side < 0 and c > min(lb, vw if _ok(vw) else lb):
                return "exit"
            return None
        if c > ub:
            return Signal(1, lb, None, None, reason="close above the noise area", evidence={"ub": ub, "lb": lb})
        if c < lb:
            return Signal(-1, ub, None, None, reason="close below the noise area", evidence={"ub": ub, "lb": lb})
        return None


class NarrowRangeORB(OpeningRangeBreakout):
    """Toby Crabel: opening-range breakouts only on the day after a narrow-range day (yesterday's
    range the smallest of the last 4 or 7 sessions): volatility compression before expansion."""
    name = "nr_orb"

    @classmethod
    def defaults(cls):
        return {**OpeningRangeBreakout.defaults(), "nr": 7}

    def on_bar_close(self, row, position):
        v = self.ctx.get(f"nr{self.params['nr']}")
        if v is None or pd.isna(v) or not bool(v):
            return None
        return super().on_bar_close(row, position)


class VolatilityBreakout(_Base):
    """Larry Williams volatility breakout: a close beyond open +/- k * yesterday's range enters in
    that direction at the next open (approximating his stop entry, slightly worse); stop at the open."""
    name = "vol_breakout"

    @classmethod
    def defaults(cls):
        return {"k": 0.35}

    def on_bar_close(self, row, position):
        if position is not None or self.entries:
            return None
        o, rg, c = row.get("sess_open"), self.ctx.get("prev_range"), row["close"]
        if not _ok(o, rg) or rg <= 0:
            return None
        k = self.params["k"]
        side = 1 if c > o + k * rg else -1 if c < o - k * rg else 0
        if not side:
            return None
        self.entries += 1
        return Signal(side, o, None, None, reason=f"broke open {'+' if side > 0 else '-'} {k} x yesterday's range",
                      evidence={"range": rg})


class Oops(_Base):
    """Larry Williams 'Oops': the market opens beyond yesterday's low (high) by min_gap and then
    trades back through that level -> trade back toward yesterday's range; stop beyond the session
    extreme by buffer_atr ATRs."""
    name = "oops"

    @classmethod
    def defaults(cls):
        return {"min_gap": 0.0, "buffer_atr": 0.25, "target_r": None}

    def on_bar_close(self, row, position):
        p = self.params
        if position is not None or self.entries:
            return None
        o, pl, ph, a, c = row.get("sess_open"), self.ctx.get("prev_low"), self.ctx.get("prev_high"), row.get("atr14"), row["close"]
        if not _ok(o, pl, ph, a) or a <= 0:
            return None
        if o < pl * (1 - p["min_gap"]) and c > pl:
            stop = row["sess_low"] - p["buffer_atr"] * a
            self.entries += 1
            return Signal(1, stop, self._target(1, c, stop, p["target_r"]), None, reason="gap below yesterday's low failed",
                          evidence={"prev_low": pl})
        if o > ph * (1 + p["min_gap"]) and c < ph:
            stop = row["sess_high"] + p["buffer_atr"] * a
            self.entries += 1
            return Signal(-1, stop, self._target(-1, c, stop, p["target_r"]), None, reason="gap above yesterday's high failed",
                          evidence={"prev_high": ph})
        return None


class TurtleSoup(_Base):
    """Linda Raschke 'Turtle Soup', intraday: price trades below the prior 20-session low (above the
    high) and closes back above (below) it -> fade the false breakout; stop beyond today's extreme."""
    name = "turtle_soup"

    @classmethod
    def defaults(cls):
        return {"buffer_atr": 0.25, "target_r": None}

    def on_bar_close(self, row, position):
        p = self.params
        if position is not None or self.entries:
            return None
        lo, hi, a, c = self.ctx.get("low20"), self.ctx.get("high20"), row.get("atr14"), row["close"]
        if not _ok(lo, hi, a) or a <= 0:
            return None
        if row["sess_low"] < lo < c:
            stop = row["sess_low"] - p["buffer_atr"] * a
            self.entries += 1
            return Signal(1, stop, self._target(1, c, stop, p["target_r"]), None, reason="false break of the 20-day low",
                          evidence={"low20": lo})
        if row["sess_high"] > hi > c:
            stop = row["sess_high"] + p["buffer_atr"] * a
            self.entries += 1
            return Signal(-1, stop, self._target(-1, c, stop, p["target_r"]), None, reason="false break of the 20-day high",
                          evidence={"high20": hi})
        return None


class CalendarLong(_Base):
    """Calendar effects: long from 9:31 to the flatten time on turn-of-month days ('tom'), the
    session before a holiday ('pre_holiday'), Mondays or Fridays; stop 1 daily ATR below."""
    name = "calendar_long"

    @classmethod
    def defaults(cls):
        return {"days": "tom"}

    def on_bar_close(self, row, position):
        if position is not None or self.entries or row["minute"] != 0:
            return None
        d, ctx = self.params["days"], self.ctx
        hit = {"tom": ctx.get("tom") is True or ctx.get("tom") == True, "pre_holiday": ctx.get("pre_holiday") == True,  # noqa: E712
               "monday": ctx.get("dow") == 0, "friday": ctx.get("dow") == 4}[d]
        a = ctx.get("atr_d14")
        if not hit or not _ok(a):
            return None
        self.entries += 1
        return Signal(1, row["close"] - a, None, None, reason=f"calendar: {d}", evidence={"days": d})


class GapGo(_Base):
    """Gap and go: an overnight gap beyond min_gap whose first N-minute candle moves further in the
    gap's direction -> follow it; stop at the session's opposite extreme."""
    name = "gap_go"

    @classmethod
    def defaults(cls):
        return {"min_gap": 0.003, "candle_min": 5}

    def on_bar_close(self, row, position):
        p = self.params
        if position is not None or self.entries or row["minute"] != p["candle_min"] - 1:
            return None
        g, o, c = self.ctx.get("gap_adj"), row.get("sess_open"), row["close"]
        if not _ok(g, o) or abs(g) < p["min_gap"]:
            return None
        side = 1 if g > 0 else -1
        if (c - o) * side <= 0.01:
            return None
        stop = row["sess_low"] if side > 0 else row["sess_high"]
        self.entries += 1
        return Signal(side, stop, None, None, reason=f"{g:+.2%} gap, first candle continues", evidence={"gap": g})



class Filtered(_Base):
    """Wraps a base strategy and passes its entries only on chosen days: vix 'high' (previous VIX
    close >= vix_level), 'low' (< vix_level) or 'any'; trend 'with' (longs only when the previous
    close is above its 20-day average, shorts only below) or 'any'. Exits always pass."""
    name = "filtered"

    @classmethod
    def defaults(cls):
        return {"base": "first_candle", "base_params": {}, "vix": "any", "vix_level": 20.0, "trend": "any"}

    def __init__(self, **params):
        super().__init__(**params)
        self.inner = REGISTRY[self.params["base"]](**dict(self.params["base_params"]))

    def on_session_start(self, day, ctx):
        super().on_session_start(day, ctx)
        self.inner.on_session_start(day, ctx)

    def on_bar_close(self, row, position):
        out = self.inner.on_bar_close(row, position)
        if not isinstance(out, Signal):
            return out
        p, ctx = self.params, self.ctx
        vx = ctx.get("vix_prev")
        if p["vix"] != "any":
            if not _ok(vx) or (p["vix"] == "high") != (vx >= p["vix_level"]):
                return None
        if p["trend"] == "with":
            tr = ctx.get("sma_d20_dist")
            if not _ok(tr) or (tr > 0) != (out.side > 0):
                return None
        return out



class EventLong(_Base):
    """Scheduled-event drift: long from 9:31 to the flatten time on days flagged `event` in the
    daily context (e.g. 'fomc' = FOMC announcement day: the pre-announcement drift of Lucca &
    Moench 2015; 'fomc_eve' = the session before); stop 1 daily ATR below."""
    name = "event_long"

    @classmethod
    def defaults(cls):
        return {"event": "fomc"}

    def on_bar_close(self, row, position):
        if position is not None or self.entries or row["minute"] != 0:
            return None
        flag, a = self.ctx.get(self.params["event"]), self.ctx.get("atr_d14")
        if flag is not True and flag != True or not _ok(a):  # noqa: E712
            return None
        self.entries += 1
        return Signal(1, row["close"] - a, None, None, reason=f"event day: {self.params['event']}")


REGISTRY = {c.name: c for c in (OpeningRangeBreakout, VWAPTrend, VWAPReversion, GapFill, MomentumBreakout,
                                LiquiditySweep, OpenDrive, FirstCandle, NoiseMomentum, NarrowRangeORB,
                                VolatilityBreakout, Oops, TurtleSoup, CalendarLong, GapGo, Filtered, EventLong)}


def make(name: str, **params: Any):
    if name not in REGISTRY and name == "ml_direction":
        from . import ml  # noqa: F401  (registers itself)
    return REGISTRY[name](**params)
