"""Persistent record of every signal, executed or not, and its outcome (SQLite).

Prediction accuracy (did price go the predicted way over the signal's horizon) is tracked
separately from trade profitability (net P&L after costs). Nothing here changes a live
strategy: it only records.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, mode TEXT NOT NULL, instrument TEXT NOT NULL, strategy TEXT NOT NULL,
  config_fingerprint TEXT, direction INTEGER NOT NULL, price REAL, entry_low REAL, entry_high REAL,
  stop REAL, target REAL, shares INTEGER, max_loss REAL, potential_profit REAL, rr REAL,
  horizon_min INTEGER, probability REAL, probability_source TEXT, regime TEXT,
  evidence TEXT, failure_modes TEXT, invalidation TEXT, status TEXT NOT NULL,
  -- outcomes, filled in later
  actual_direction INTEGER, entry_fill REAL, exit_fill REAL, exit_reason TEXT, mfe REAL, mae REAL,
  pnl REAL, costs REAL, resolved_ts TEXT
);
CREATE INDEX IF NOT EXISTS ix_signals_ts ON signals(ts);
"""


class SignalStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def add(self, **row: Any) -> int:
        for k in ("evidence", "failure_modes", "invalidation"):
            if k in row and not isinstance(row[k], str):
                row[k] = json.dumps(row[k], default=str)
        if row.get("probability") is not None and not row.get("probability_source"):
            raise ValueError("a probability needs its calibration source (no invented confidence numbers)")
        cols = ", ".join(row)
        q = ", ".join("?" for _ in row)
        with self._db() as db:
            cur = db.execute(f"INSERT INTO signals ({cols}) VALUES ({q})", list(row.values()))
            return int(cur.lastrowid)

    def resolve(self, signal_id: int, **outcome: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in outcome)
        with self._db() as db:
            db.execute(f"UPDATE signals SET {sets} WHERE id = ?", [*outcome.values(), signal_id])

    def all(self) -> list[dict[str, Any]]:
        with self._db() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM signals ORDER BY ts")]
