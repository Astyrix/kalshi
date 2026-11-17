"""Post-mortem memory: per-setup track record plus a log of lessons.

Each trade is filed under a setup bucket (time left, whether momentum agreed,
how expensive the contract was). A bucket that keeps losing gets a higher
entry threshold, and one that does well earns its threshold back. Every
trade also gets a post-mortem (premature entry, early exit, false breakout,
thesis broke) that is kept in a rolling lessons log.
"""
from __future__ import annotations

import json
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path


def bucket(tau: int, aligned: bool, ask: float) -> str:
    t = "late" if tau < 240 else "mid" if tau < 540 else "early"
    a = "aligned" if aligned else "against"
    p = "cheap" if ask < 0.85 else "mid" if ask < 0.92 else "rich"
    return f"{t}|{a}|{p}"


@dataclass
class Memory:
    base_threshold: float = 0.85
    stats: dict[str, dict] = field(default_factory=dict)
    adjust: dict[str, float] = field(default_factory=dict)
    mistakes: Counter = field(default_factory=Counter)
    lessons: deque = field(default_factory=lambda: deque(maxlen=200))
    trades: int = 0
    wins: int = 0
    pnl: float = 0.0
    missed: int = 0

    def threshold(self, key: str) -> float:
        return min(self.base_threshold + self.adjust.get(key, 0.0), 0.97)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    def last_lesson(self) -> str:
        return self.lessons[-1] if self.lessons else "none yet"

    def record(self, key: str, pnl: float, entry: float, notes: list[str]):
        st = self.stats.setdefault(key, {"n": 0, "wins": 0, "pnl": 0.0, "cost": 0.0})
        st["n"] += 1
        st["wins"] += pnl > 0
        st["pnl"] += pnl
        st["cost"] += entry
        self.trades += 1
        self.wins += pnl > 0
        self.pnl += pnl
        for n in notes:
            self.mistakes[n.split(":")[0]] += 1
            self.lessons.append(f"[{key}] {n}")
        # Re-tune this bucket once it has a meaningful sample.
        if st["n"] >= 15:
            roi = st["pnl"] / max(st["cost"], 1e-9)
            cur = self.adjust.get(key, 0.0)
            if roi < 0:
                new = min(cur + 0.02, 0.12)
            elif roi > 0.05:
                new = max(cur - 0.01, -0.03)
            else:
                new = cur
            if new != cur:
                self.adjust[key] = round(new, 3)
                self.lessons.append(f"[{key}] threshold {self.threshold(key):.2f} after "
                                    f"{st['n']} trades, ROI {roi:+.1%}")

    def save(self, path: Path):
        d = dict(self.__dict__, mistakes=dict(self.mistakes), lessons=list(self.lessons))
        path.write_text(json.dumps(d, indent=2))

    @classmethod
    def load(cls, path: Path, base_threshold: float = 0.85) -> "Memory":
        if not path.exists():
            return cls(base_threshold=base_threshold)
        d = json.loads(path.read_text())
        d["mistakes"] = Counter(d.get("mistakes", {}))
        d["lessons"] = deque(d.get("lessons", []), maxlen=200)
        d["base_threshold"] = base_threshold
        return cls(**d)
