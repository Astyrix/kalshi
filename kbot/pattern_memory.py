"""Running scorecard for every candle / chart pattern, kept up to date live.

Each time a pattern prints on a 1m / 5m / 15m candle, it is remembered with
the price; once its horizon has passed, the actual move (in units of normal
volatility) is scored. Stats start from the historical study
(reports/patterns.json) and every live observation is added on top, so the
bot keeps learning which patterns work right now and flags ones that have
stopped working. Trades also record which patterns were showing at entry,
so the post-mortem can say which patterns made or lost money.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .patterns import CHART_PATTERNS, PATTERNS, chart_patterns, detect

HORIZON_S = {"1m": 300, "5m": 900, "15m": 900}
TF_S = {"1m": 60, "5m": 300, "15m": 900}


class PatternMemory:
    def __init__(self, path: Path, prior_path: Path = Path("reports/patterns.json")):
        self.path = path
        self.stats: dict[str, dict] = {}      # "tf:pattern" -> {hist..., live_n, live_sum, live_sq, live_up}
        self.pending: list[dict] = []
        self.trades: dict[str, list[float]] = {}
        if prior_path.exists():
            prior = json.loads(prior_path.read_text())
            for tf in TF_S:
                for p, e in prior.get(f"index:{tf}", {}).items():
                    self.stats[f"{tf}:{p}"] = {"hist_n": e["n"], "hist_mean": e["mean_move"],
                                               "hist_t": e["t"], "hist_reliable": e["reliable"]}
        if path.exists():
            d = json.loads(path.read_text())
            for k, v in d.get("stats", {}).items():
                self.stats.setdefault(k, {}).update({kk: vv for kk, vv in v.items() if kk.startswith("live")})
            self.pending = d.get("pending", [])
            self.trades = d.get("trades", {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"stats": self.stats, "pending": self.pending[-2000:],
                                         "trades": self.trades}, indent=1))

    # -- observing ------------------------------------------------------------
    def observe(self, series, t: int, sig_per_sec: float) -> list[str]:
        """Detect patterns on candles that just completed at t; returns their keys."""
        found = []
        x = series.at(t)
        if math.isnan(x):
            return found
        for tf, sec in TF_S.items():
            if t % sec:
                continue                          # only right after a candle closes
            cs, _ = series.candles(t, sec, 30)
            unit = sig_per_sec * x * math.sqrt(sec)
            hits = {**{f"pat_{p}": v for p, v in detect(cs).items()},
                    **{f"chart_{p}": v for p, v in chart_patterns(cs, unit).items()}}
            for p, v in hits.items():
                if v:
                    key = f"{tf}:{p}"
                    found.append(key)
                    self.pending.append({"key": key, "t": t, "x": x,
                                         "unit": sig_per_sec * x * math.sqrt(HORIZON_S[tf]),
                                         "due": t + HORIZON_S[tf]})
        return found

    def resolve(self, series, now: int) -> list[dict]:
        """Score detections whose horizon has passed."""
        done, keep = [], []
        for p in self.pending:
            if p["due"] > now:
                keep.append(p)
                continue
            y = series.at(p["due"])
            if math.isnan(y) or p["unit"] <= 0:
                continue
            move = (y - p["x"]) / p["unit"]
            s = self.stats.setdefault(p["key"], {})
            s["live_n"] = s.get("live_n", 0) + 1
            s["live_sum"] = s.get("live_sum", 0.0) + move
            s["live_sq"] = s.get("live_sq", 0.0) + move * move
            s["live_up"] = s.get("live_up", 0) + (move > 0)
            done.append({"pattern": p["key"], "move_sigma": round(move, 3)})
        self.pending = keep
        return done

    def record_trade(self, patterns: list[str], pnl_per_contract: float):
        for k in patterns:
            self.trades.setdefault(k, []).append(round(pnl_per_contract, 4))

    # -- reading ------------------------------------------------------------
    def view(self, key: str) -> dict:
        s = self.stats.get(key, {})
        tf, name = key.split(":", 1)
        textbook = {**PATTERNS, **CHART_PATTERNS}.get(name.split("_", 1)[1], 0)
        out = {"pattern": key, "textbook": {1: "bullish", -1: "bearish", 0: "neutral"}[textbook]}
        if "hist_n" in s:
            out["history"] = {"n": s["hist_n"], "avg_move_sigma": s["hist_mean"], "t": s["hist_t"],
                              "reliable": s["hist_reliable"]}
        n = s.get("live_n", 0)
        if n:
            m = s["live_sum"] / n
            sd = math.sqrt(max(s["live_sq"] / n - m * m, 1e-9))
            out["live"] = {"n": n, "avg_move_sigma": round(m, 3), "up_rate": round(s["live_up"] / n, 3),
                           "t": round(m / (sd / math.sqrt(n)), 2) if n > 2 else None}
            hm = s.get("hist_mean")
            if hm is not None and n >= 30 and s.get("hist_reliable") and m * hm < 0:
                out["warning"] = "live behaviour has flipped vs history"
        tr = self.trades.get(key)
        if tr:
            out["trades"] = {"n": len(tr), "avg_pnl_per_contract": round(sum(tr) / len(tr), 4)}
        return out

    def lean(self, keys: list[str]) -> float:
        """Combined expected move (sigma) from reliable patterns showing now."""
        tot = 0.0
        for k in keys:
            s = self.stats.get(k, {})
            n = s.get("live_n", 0)
            if s.get("hist_reliable") or n >= 50:
                # shrink live toward history: history counts as up to 200 observations
                hn = min(s.get("hist_n", 0), 200)
                hm = s.get("hist_mean", 0.0)
                tot += (hm * hn + s.get("live_sum", 0.0)) / max(hn + n, 1)
        return tot

    def scoreboard(self, min_n: int = 10) -> list[dict]:
        rows = [self.view(k) for k, s in self.stats.items() if s.get("live_n", 0) >= min_n]
        return sorted(rows, key=lambda r: -abs(r["live"]["avg_move_sigma"]))
