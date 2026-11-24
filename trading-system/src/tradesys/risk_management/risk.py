"""Position sizing, loss limits and the persistent kill switch.

Rules (defaults in config/default.yaml, risk:):
  size        shares = floor(equity * risk_per_trade / per-share risk), where per-share risk is
              |entry - stop| plus the estimated round-trip cost; capped by max_leverage * equity.
  daily       new entries stop for the day once the day's P&L <= -max_daily_loss * day-start equity
  weekly      ... or the week's P&L <= -max_weekly_loss * week-start equity (ISO week)
  streak      ... or after max_consecutive_losses losing trades in a row (resets next session)
  count       ... or after max_trades_per_session entries
  positions   at most max_positions open at once
No martingale, no averaging down: size never depends on the last trade's outcome.

The kill switch is a file. Once tripped it stays tripped across restarts until a person
resets it (`main.py kill-switch --reset --by NAME`); while tripped nothing may open a position.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from ..config import RiskCfg


class KillSwitch:
    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None
        self._mem: dict | None = None

    def state(self) -> dict:
        if self.path is None:
            return self._mem or {"tripped": False}
        if not self.path.exists():
            return {"tripped": False}
        try:
            return json.loads(self.path.read_text())
        except Exception:
            # an unreadable state file fails safe: treat as tripped
            return {"tripped": True, "reason": f"kill-switch file {self.path} unreadable"}

    @property
    def tripped(self) -> bool:
        return bool(self.state().get("tripped"))

    def trip(self, reason: str, when: str | None = None) -> None:
        s = self.state()
        if s.get("tripped"):
            return                                    # keep the first reason
        s = {"tripped": True, "reason": reason, "when": when or dt.datetime.now(dt.timezone.utc).isoformat()}
        if self.path is None:
            self._mem = s
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1))
        tmp.replace(self.path)

    def reset(self, by: str) -> None:
        if not by.strip():
            raise ValueError("a manual reset needs the name of the person resetting")
        s = {"tripped": False, "reset_by": by, "reset_at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "previous": self.state()}
        if self.path is None:
            self._mem = s
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(s, indent=1))


@dataclass
class RiskState:
    equity: float
    day: dt.date | None = None
    day_start_equity: float = 0.0
    week: tuple[int, int] | None = None
    week_start_equity: float = 0.0
    day_pnl: float = 0.0
    week_pnl: float = 0.0
    trades_today: int = 0
    streak: int = 0                    # consecutive losing trades today
    open_positions: int = 0
    halted_today: str | None = None
    log: list[dict] = field(default_factory=list)


class RiskManager:
    def __init__(self, cfg: RiskCfg, kill: KillSwitch | None = None, equity: float | None = None):
        self.cfg = cfg
        self.kill = kill or KillSwitch(None)
        self.s = RiskState(equity=equity if equity is not None else cfg.starting_equity)

    # ---- session bookkeeping
    def start_session(self, day: dt.date) -> None:
        s = self.s
        wk = tuple(day.isocalendar()[:2])
        if s.week != wk:
            s.week, s.week_start_equity, s.week_pnl = wk, s.equity, 0.0
        s.day, s.day_start_equity, s.day_pnl = day, s.equity, 0.0
        s.trades_today, s.streak, s.halted_today = 0, 0, None
        if s.week_pnl <= -self.cfg.max_weekly_loss * s.week_start_equity:
            self._halt(f"weekly loss limit ({self.cfg.max_weekly_loss:.2%}) reached earlier this week")

    def _halt(self, why: str) -> None:
        if not self.s.halted_today:
            self.s.halted_today = why
            self.s.log.append({"day": str(self.s.day), "halt": why})

    # ---- checks
    def can_enter(self) -> tuple[bool, str]:
        s, c = self.s, self.cfg
        if self.kill.tripped:
            return False, "kill switch: " + str(self.kill.state().get("reason"))
        if s.halted_today:
            return False, s.halted_today
        if s.open_positions >= c.max_positions:
            return False, f"max positions ({c.max_positions}) open"
        if s.trades_today >= c.max_trades_per_session:
            self._halt(f"max trades per session ({c.max_trades_per_session}) reached")
            return False, s.halted_today
        return True, ""

    def size(self, entry: float, stop: float, cost_per_share: float) -> int:
        """Shares to buy/sell so that a stop filled at its price loses about risk_per_trade of equity."""
        per = abs(entry - stop) + max(cost_per_share, 0.0)
        if per <= 0 or not math.isfinite(per) or entry <= 0:
            return 0
        by_risk = math.floor(self.s.equity * self.cfg.risk_per_trade / per)
        by_cash = math.floor(self.s.equity * self.cfg.max_leverage / entry)
        return max(0, min(by_risk, by_cash))

    # ---- events
    def on_entry(self) -> None:
        self.s.trades_today += 1
        self.s.open_positions += 1

    def on_exit(self, pnl: float) -> None:
        s, c = self.s, self.cfg
        s.open_positions = max(0, s.open_positions - 1)
        s.equity += pnl
        s.day_pnl += pnl
        s.week_pnl += pnl
        s.streak = s.streak + 1 if pnl < 0 else 0
        if s.day_pnl <= -c.max_daily_loss * s.day_start_equity:
            self._halt(f"daily loss limit ({c.max_daily_loss:.2%}) reached")
        if s.week_pnl <= -c.max_weekly_loss * s.week_start_equity:
            self._halt(f"weekly loss limit ({c.max_weekly_loss:.2%}) reached")
        if s.streak >= c.max_consecutive_losses:
            self._halt(f"{s.streak} consecutive losses")

    def on_anomaly(self, why: str, trip: bool = False, when: str | None = None) -> None:
        """Stale data, abnormal spread, volatility spike: halt today; trip the switch if severe."""
        self._halt(why)
        if trip:
            self.kill.trip(why, when)
