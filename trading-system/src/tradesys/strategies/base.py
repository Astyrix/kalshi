"""Strategy interface.

A strategy sees one row of causal features at the close of each regular-session bar and may
return a Signal (open a position at the next bar's open), "exit" (close the open position at
the next bar's open), or None. It never sees later rows. It never sizes positions or checks
risk: the engine and the risk manager do.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd


@dataclass(frozen=True)
class Signal:
    side: int                          # +1 long, -1 short
    stop: float                        # stop price (required: no trade without a defined loss)
    target: float | None = None        # limit take-profit price, optional
    max_hold_min: int | None = None    # time stop in minutes after the fill
    limit: float | None = None         # entry limit price; None = market order at the next open
    limit_expiry_min: int = 5          # a limit entry not filled within this many minutes is cancelled
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.side not in (1, -1):
            raise ValueError("side must be +1 or -1")
        if self.limit is not None and (self.limit - self.stop) * self.side <= 0:
            raise ValueError("a limit entry must be on the profitable side of its stop")


@dataclass
class Position:
    side: int
    shares: int
    entry: float
    entry_time: pd.Timestamp
    stop: float
    target: float | None
    max_hold_min: int | None
    strategy: str
    reason: str
    signal_time: pd.Timestamp
    risk_per_share: float
    evidence: dict[str, Any]
    mfe: float = 0.0                   # best price move in our favour, $/share
    mae: float = 0.0                   # worst move against us, $/share (positive number)


class Strategy(Protocol):
    name: str
    params: dict[str, Any]

    def on_session_start(self, day: dt.date, ctx: pd.Series) -> None: ...

    def on_bar_close(self, row: pd.Series, position: Position | None) -> Signal | str | None: ...
