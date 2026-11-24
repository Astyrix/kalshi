"""Event-driven intraday backtester for SPY shares, one session at a time, bar by bar.

Order of events inside each 1-minute bar t (bars labelled by their start time):
  1. at t's open: mandatory flatten (t >= flatten_start), a strategy exit requested at the
     previous close, a time stop, then a pending entry (from a signal at the previous close)
     fills at t's open plus half spread and slippage. An entry is cancelled if t is after the
     entry cutoff, if the risk manager refuses, if the open is already beyond the stop or the
     target, or if no shares fit (size, buying power, or the bar's volume cap).
  2. during t: stop and target against t's high and low. A bar that opens beyond the stop
     fills at the open (gap). A target (limit) fills only if the market trades at least one
     tick through it. If a bar touches both, the stop is assumed first (conservative), since
     1-minute bars do not say which came first.
  3. at t's close: the strategy sees row t and may signal; the order goes out at t + 1 (plus
     latency_bars).
Anything still open when the hard flat time arrives (missing bars) is closed at the last
price with the extreme cost scenario and logged as an emergency.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..config import Config
from ..market_calendar import NY, window
from ..risk_management.risk import KillSwitch, RiskManager
from ..strategies.base import Position, Signal, Strategy
from .costs import CostModel

MIN = pd.Timedelta(minutes=1)


@dataclass
class Trade:
    day: dt.date
    strategy: str
    side: int
    shares: int
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    entry: float                  # fill price incl. spread/slippage
    entry_raw: float              # the bar open it filled against
    exit_time: pd.Timestamp
    exit: float
    exit_raw: float
    exit_reason: str
    stop: float
    target: float | None
    gross: float                  # side * (exit - entry) * shares, at fill prices
    costs: float                  # commissions + regulatory fees
    net: float
    frictionless: float           # side * (exit_raw - entry_raw) * shares: no spread, slippage or fees
    r_multiple: float             # net / (planned risk in $)
    mfe_r: float
    mae_r: float
    hold_min: float
    reason: str
    evidence: dict = field(default_factory=dict)


@dataclass
class Result:
    trades: pd.DataFrame
    signals: pd.DataFrame
    daily: pd.Series              # net P&L per session (0 on no-trade days)
    equity: pd.Series             # equity at each session's end
    halts: list[dict]
    emergencies: list[dict]
    scenario: str
    starting_equity: float


class Backtester:
    def __init__(self, cfg: Config, strategy: Strategy, scenario: str = "normal",
                 kill_path: str | None = None, tick: float = 0.01, max_gap_min: int = 3):
        self.cfg, self.strat, self.scenario = cfg, strategy, scenario
        self.cost = CostModel.from_cfg(cfg.costs, scenario)
        self.risk = RiskManager(cfg.risk, KillSwitch(kill_path))
        self.tick, self.max_gap = tick, pd.Timedelta(minutes=max_gap_min)
        self.trades: list[Trade] = []
        self.signals: list[dict] = []
        self.emergencies: list[dict] = []
        self.daily: dict[dt.date, float] = {}
        self.eq: dict[dt.date, float] = {}

    # ------------------------------------------------------------------ exits
    def _close(self, pos: Position, t: pd.Timestamp, raw: float, fill: float, why: str, day: dt.date,
               extra_cost: float = 0.0) -> None:
        shares = pos.shares
        c = self.cost
        gross = pos.side * (fill - pos.entry) * shares
        sell_price = fill if pos.side > 0 else pos.entry           # the sale leg: exit for longs, entry for shorts
        costs = c.commission(shares) * 2 + c.sale_fees(shares, sell_price) + extra_cost
        net = gross - costs
        risk_usd = max(pos.risk_per_share * shares, 1e-9)
        self.trades.append(Trade(
            day=day, strategy=pos.strategy, side=pos.side, shares=shares, signal_time=pos.signal_time,
            entry_time=pos.entry_time, entry=pos.entry, entry_raw=pos.evidence.get("_entry_raw", pos.entry),
            exit_time=t, exit=fill, exit_raw=raw, exit_reason=why, stop=pos.stop, target=pos.target,
            gross=gross, costs=costs, net=net,
            frictionless=pos.side * (raw - pos.evidence.get("_entry_raw", pos.entry)) * shares,
            r_multiple=net / risk_usd, mfe_r=pos.mfe / max(pos.risk_per_share, 1e-9),
            mae_r=pos.mae / max(pos.risk_per_share, 1e-9), hold_min=(t - pos.entry_time).total_seconds() / 60,
            reason=pos.reason, evidence={k: v for k, v in pos.evidence.items() if not k.startswith("_")}))
        self.risk.on_exit(net)

    def _sig(self, t, s: Signal, est: float, status: str) -> None:
        self.signals.append({"time": t, "strategy": self.strat.name, "side": s.side, "est_price": est,
                             "stop": s.stop, "target": s.target, "reason": s.reason, "status": status,
                             **{f"ev_{k}": v for k, v in s.evidence.items()}})

    # ------------------------------------------------------------------ session
    def run_session(self, day: dt.date, f: pd.DataFrame, ctx: pd.Series) -> None:
        w = window(day, self.cfg.session)
        rows = f[(f.index >= w.open) & (f.index < w.hard_flat)]
        self.risk.start_session(day)
        self.strat.on_session_start(day, ctx)
        pos: Position | None = None
        pending: tuple[Signal, pd.Timestamp, float, int] | None = None   # signal, signal time, est price, fire index
        exit_next = False
        n = len(rows)
        if n == 0:
            self.daily[day] = 0.0
            self.eq[day] = self.risk.s.equity
            return
        o, h, l, c = (rows[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        v = rows["volume"].to_numpy(float)
        atr = rows["atr14"].to_numpy(float) if "atr14" in rows else np.full(n, np.nan)
        times = rows.index
        recs = rows.to_dict("records")              # plain dicts: far faster than rows.iloc[i] per bar
        start_eq = self.risk.s.equity
        for i in range(n):
            t = times[i]
            if i and t - times[i - 1] > self.max_gap:
                self.risk.on_anomaly(f"data gap of {(t - times[i - 1]).total_seconds() / 60:.0f} min before {t:%H:%M}")
                if pending:
                    self._sig(pending[1], pending[0], pending[2], "cancelled: data gap")
                    pending = None
            # 1) at the open
            if pos is not None:
                why = None
                if t >= w.flatten_start:
                    why = "flatten"
                elif exit_next:
                    why = "strategy exit"
                elif pos.max_hold_min and t >= pos.entry_time + pd.Timedelta(minutes=pos.max_hold_min):
                    why = "time stop"
                if why:
                    self._close(pos, t, o[i], self.cost.market_fill(-pos.side, o[i]), why, day)
                    pos, exit_next = None, False
            if pending is not None and i >= pending[3]:
                s, st, est, _ = pending
                pending = None
                status = self._try_enter(s, t, o[i], v[i], est, w, st)
                if isinstance(status, Position):
                    pos = status
                    self._sig(st, s, est, "filled")
                else:
                    self._sig(st, s, est, status)
            # 2) during the bar
            if pos is not None:
                ref = pos.evidence["_entry_raw"]
                if pos.side > 0:
                    pos.mfe, pos.mae = max(pos.mfe, h[i] - ref), max(pos.mae, ref - l[i])
                    stop_hit, tgt_hit = l[i] <= pos.stop, pos.target is not None and h[i] >= pos.target + self.tick
                else:
                    pos.mfe, pos.mae = max(pos.mfe, ref - l[i]), max(pos.mae, h[i] - ref)
                    stop_hit, tgt_hit = h[i] >= pos.stop, pos.target is not None and l[i] <= pos.target - self.tick
                if stop_hit:
                    raw = min(pos.stop, o[i]) if pos.side > 0 else max(pos.stop, o[i])
                    self._close(pos, t + MIN * 0.5, raw, self.cost.stop_fill(-pos.side, pos.stop, o[i]),
                                "stop (gap)" if raw != pos.stop else "stop", day)
                    pos, exit_next = None, False
                elif tgt_hit:
                    self._close(pos, t + MIN * 0.5, pos.target, pos.target, "target", day)
                    pos, exit_next = None, False
            # anomaly: a bar far wider than normal halts new entries for the day
            rng = h[i] - l[i]
            if math.isfinite(atr[i]) and atr[i] > 0 and rng > self.cfg.risk.max_bar_range_atr * atr[i]:
                self.risk.on_anomaly(f"{t:%H:%M} bar range {rng:.2f} > {self.cfg.risk.max_bar_range_atr} x ATR")
            # 3) at the close
            out = self.strat.on_bar_close(recs[i], pos)
            if pos is not None and out == "exit":
                exit_next = True
            elif pos is None and isinstance(out, Signal) and pending is None:
                st = t + MIN
                if st > w.entry_cutoff:
                    self._sig(st, out, c[i], "rejected: after entry cutoff")
                else:
                    pending = (out, st, c[i], i + 1 + self.cost.latency_bars)
        if pending is not None:
            self._sig(pending[1], pending[0], pending[2], "cancelled: no later bar")
        if pos is not None:                                   # bars missing up to the hard flat time
            ext = CostModel.from_cfg(self.cfg.costs, "extreme")
            self._close(pos, times[-1] + MIN, c[-1], ext.market_fill(-pos.side, c[-1]), "EMERGENCY flatten", day)
            self.emergencies.append({"day": str(day), "why": "position open at the end of available bars"})
        self.daily[day] = self.risk.s.equity - start_eq
        self.eq[day] = self.risk.s.equity

    def _try_enter(self, s: Signal, t: pd.Timestamp, bar_open: float, bar_vol: float, est: float, w,
                   sig_time: pd.Timestamp) -> Position | str:
        if t < w.first_entry or t > w.entry_cutoff:
            return "cancelled: outside the entry window"
        if s.side < 0 and not self.cfg.risk.allow_short:
            return "rejected: shorting disabled"
        ok, why = self.risk.can_enter()
        if not ok:
            return "rejected: " + why
        if (s.side > 0 and bar_open <= s.stop) or (s.side < 0 and bar_open >= s.stop):
            return "cancelled: opened beyond the stop"
        if s.target is not None and ((s.side > 0 and bar_open >= s.target) or (s.side < 0 and bar_open <= s.target)):
            return "cancelled: opened beyond the target"
        fill = self.cost.market_fill(s.side, bar_open)
        shares = self.risk.size(est, s.stop, self.cost.round_trip_per_share(est))
        cap = math.floor(self.cost.participation_cap * bar_vol) if self.cost.participation_cap > 0 else shares
        if shares < 1:
            return "rejected: size rounds to zero"
        part = ""
        if shares > cap:
            if cap < 1:
                return "rejected: no volume in the fill bar"
            shares, part = cap, " (partial: volume cap)"
        self.risk.on_entry()
        ev = dict(s.evidence, _entry_raw=bar_open)
        if part:
            ev["partial_fill"] = True
        return Position(side=s.side, shares=shares, entry=fill, entry_time=t, stop=s.stop, target=s.target,
                        max_hold_min=s.max_hold_min, strategy=self.strat.name, reason=s.reason + part,
                        signal_time=sig_time, risk_per_share=abs(bar_open - s.stop), evidence=ev)

    # ------------------------------------------------------------------ run
    def run(self, feats: pd.DataFrame, ctx: pd.DataFrame, days: list[dt.date] | None = None,
            skip_days: set[dt.date] | None = None) -> Result:
        by_day = {d: g for d, g in feats.groupby("day")}
        days = days if days is not None else sorted(by_day)
        for d in days:
            if skip_days and d in skip_days:
                continue
            if d not in by_day or d not in ctx.index:
                continue
            self.run_session(d, by_day[d], ctx.loc[d])
        tr = pd.DataFrame([asdict(t) for t in self.trades])
        sg = pd.DataFrame(self.signals)
        daily = pd.Series(self.daily, dtype=float).sort_index()
        eq = pd.Series(self.eq, dtype=float).sort_index()
        return Result(tr, sg, daily, eq, list(self.risk.s.log), self.emergencies, self.scenario,
                      self.cfg.risk.starting_equity)
