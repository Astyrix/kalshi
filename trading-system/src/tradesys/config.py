"""Typed configuration, loaded from config/default.yaml (and optional overrides)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parents[2]          # trading-system/


def _hhmm(v: Any) -> dt.time:
    if isinstance(v, dt.time):
        return v
    h, m = str(v).split(":")
    return dt.time(int(h), int(m))


class SessionCfg(BaseModel):
    timezone: str = "America/New_York"
    open: dt.time = dt.time(9, 30)
    first_entry: dt.time = dt.time(9, 31)
    entry_cutoff: dt.time = dt.time(10, 50)
    flatten_start: dt.time = dt.time(10, 55)
    hard_flat: dt.time = dt.time(11, 0)
    premarket_start: dt.time = dt.time(4, 0)

    @field_validator("open", "first_entry", "entry_cutoff", "flatten_start", "hard_flat", "premarket_start",
                     mode="before")
    @classmethod
    def _t(cls, v: Any) -> dt.time:
        return _hhmm(v)

    def check(self) -> None:
        order = [self.open, self.first_entry, self.entry_cutoff, self.flatten_start, self.hard_flat]
        if order != sorted(order) or len(set(order)) != len(order):
            raise ValueError("session times must satisfy open < first_entry < entry_cutoff < flatten_start < hard_flat")


class CostScenario(BaseModel):
    half_spread: float            # $ per share paid crossing half the spread
    slippage: float               # $ per share beyond the touch on market/stop orders
    commission_per_share: float
    min_commission: float = 0.0


class CostCfg(BaseModel):
    scenarios: dict[str, CostScenario]
    sec_fee_per_dollar_sold: float = 0.0000278
    finra_taf_per_share_sold: float = 0.000166
    finra_taf_max: float = 8.30
    participation_cap: float = 0.10
    latency_bars: int = 0


class RiskCfg(BaseModel):
    starting_equity: float = 100_000
    risk_per_trade: float = Field(0.0025, gt=0, le=0.02)
    max_daily_loss: float = Field(0.01, gt=0, le=0.05)
    max_weekly_loss: float = Field(0.03, gt=0, le=0.10)
    max_consecutive_losses: int = 3
    max_positions: int = 1
    max_trades_per_session: int = 5
    max_leverage: float = Field(1.0, gt=0, le=4.0)
    allow_short: bool = True
    kill_switch_file: str = "state/kill_switch.json"
    stale_data_seconds: float = 90
    max_spread: float = 0.05
    max_bar_range_atr: float = 6.0


class ValidationCfg(BaseModel):
    in_sample: tuple[dt.date, dt.date]
    out_of_sample: tuple[dt.date, dt.date]
    frozen_dir: str = "config/frozen"


class Config(BaseModel):
    session: SessionCfg = SessionCfg()
    validation: ValidationCfg
    costs: CostCfg
    risk: RiskCfg = RiskCfg()
    data: dict[str, Any] = {}

    def fingerprint(self) -> str:
        """Stable hash of the whole configuration (used when freezing a strategy)."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(*overrides: Path | str | dict) -> Config:
    raw = yaml.safe_load((ROOT / "config" / "default.yaml").read_text())
    for o in overrides:
        o = yaml.safe_load(Path(o).read_text()) if not isinstance(o, dict) else o
        raw = _merge(raw, o)
    cfg = Config.model_validate(raw)
    cfg.session.check()
    return cfg
