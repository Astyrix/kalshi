"""Real-time paper trading on real SPY 1-minute bars (Yahoo), 9:30-11:00 New York.

PAPER ONLY. Nothing here talks to a broker. Each minute during the window it refetches the
session's bars, rebuilds causal features, and replays the day so far through the SAME
backtesting engine (so live and historical rules cannot drift apart): a signal at a bar's
close fills at the next bar's open plus costs, stops/targets/flatten as in the backtest.
Every strategy runs in shadow mode with its parameters as given; none is validated yet unless
it comes from a frozen spec, and each line says which.

Output per minute and strategy: one JSON decision line (stdout and reports/paper/decisions.jsonl).
After 11:00: the day's paper trades and every signal go to the signal store
(state/signals.db) and reports/paper/trades.jsonl.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd

from ..backtesting.engine import Backtester
from ..config import ROOT, Config
from ..data_ingestion.free_sources import yahoo_spy_minutes
from ..feature_engineering.features import daily_context, intraday
from ..market_calendar import NY, is_session, window
from ..signal_engine.store import SignalStore
from ..strategies.library import make

log = logging.getLogger(__name__)


class PaperLive:
    def __init__(self, cfg: Config, strategies: list[dict[str, Any]], out: Path | None = None):
        """strategies: [{"name": "orb", "params": {...}, "validated": False, "spec": None}, ...]"""
        self.cfg, self.specs = cfg, strategies
        self.out = out or ROOT / "reports" / "paper"
        self.out.mkdir(parents=True, exist_ok=True)
        self.store = SignalStore(ROOT / "state" / "signals.db")
        self.last_state: dict[str, str] = {}

    def _bars(self) -> pd.DataFrame:
        b = yahoo_spy_minutes(29)
        b = b[b.index.second == 0]                       # drop Yahoo's live partial-minute tick row
        return b[["open", "high", "low", "close", "volume"]].astype(float)

    def step(self, now: pd.Timestamp, final: bool = False) -> list[dict]:
        day = now.tz_convert(NY).date()
        w = window(day, self.cfg.session)
        bars = self._bars()
        last_bar = bars.index[-1].tz_convert(NY)
        closed = bars[bars.index.tz_convert(NY) + pd.Timedelta(minutes=1) <= now.tz_convert(NY)]
        stale = (now.tz_convert(NY) - (last_bar + pd.Timedelta(minutes=1))).total_seconds()
        feats, ctx = intraday(closed), daily_context(closed)
        today = feats[feats["day"] == day]
        lines = []
        for sp in self.specs:
            bt = Backtester(self.cfg, make(sp["name"], **sp["params"]), "normal")
            if stale > self.cfg.risk.stale_data_seconds and not final:
                bt.risk.on_anomaly(f"stale data: last bar {last_bar:%H:%M}, {stale:.0f}s old")
            res = bt.run(today, ctx, [day]) if len(today) and day in ctx.index else None
            tr = res.trades if res is not None else pd.DataFrame()
            sg = res.signals if res is not None else pd.DataFrame()
            open_pos = None
            if res is not None and not final:
                # a position is open if the last filled signal has no trade closed after it
                filled = sg[sg["status"] == "filled"] if len(sg) else sg
                if len(filled) > len(tr):
                    open_pos = filled.iloc[-1].to_dict()
            state = ("IN_POSITION" if open_pos else "FLAT")
            px = float(today["close"].iloc[-1]) if len(today) else None
            rec = {"timestamp": now.tz_convert(NY).isoformat(), "instrument": "SPY", "mode": "PAPER (shadow)",
                   "strategy": sp["name"], "params": sp["params"],
                   "validated": bool(sp.get("validated")), "spec": sp.get("spec"),
                   "price": px, "state": state, "data_age_s": round(stale, 1),
                   "trades_today": len(tr), "pnl_today": float(tr["net"].sum()) if len(tr) else 0.0,
                   "halts": res.halts if res is not None else [],
                   "position": None if not open_pos else {
                       "side": "long" if open_pos["side"] > 0 else "short", "stop": open_pos["stop"],
                       "target": open_pos["target"], "signal_time": str(open_pos["time"]),
                       "reason": open_pos["reason"]},
                   "window": {"entries_until": f"{w.entry_cutoff:%H:%M}", "flat_by": f"{w.flatten_start:%H:%M}"}}
            key = sp["name"] + json.dumps(sp["params"], sort_keys=True)
            changed = self.last_state.get(key) != json.dumps([state, len(tr)])
            self.last_state[key] = json.dumps([state, len(tr)])
            lines.append(rec)
            with (self.out / "decisions.jsonl").open("a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
            if changed or final:
                print(json.dumps(rec, default=str), flush=True)
            if final and res is not None:
                self._record(day, sp, res)
        return lines

    def _record(self, day: dt.date, sp: dict, res) -> None:
        for _, s in res.signals.iterrows():
            self.store.add(ts=str(s["time"]), mode="paper", instrument="SPY", strategy=sp["name"],
                           config_fingerprint=self.cfg.fingerprint(), direction=int(s["side"]), price=float(s["est_price"]),
                           stop=float(s["stop"]), target=None if pd.isna(s["target"]) else float(s["target"]),
                           evidence={k: v for k, v in s.items() if str(k).startswith("ev_")}, status=str(s["status"]),
                           failure_modes=["unvalidated strategy (shadow)"] if not sp.get("validated") else [],
                           invalidation={"stop": float(s["stop"])})
        with (self.out / "trades.jsonl").open("a") as fh:
            for _, t in res.trades.iterrows():
                fh.write(json.dumps({"day": str(day), "params": sp["params"], "validated": bool(sp.get("validated")),
                                     **{k: (str(v) if isinstance(v, (pd.Timestamp, dt.date)) else v)
                                        for k, v in t.items() if k != "evidence"}}, default=str) + "\n")
        with (self.out / "days.jsonl").open("a") as fh:
            fh.write(json.dumps({"day": str(day), "strategy": sp["name"], "params": sp["params"],
                                 "trades": len(res.trades), "net": float(res.trades["net"].sum()) if len(res.trades) else 0.0,
                                 "halts": res.halts}, default=str) + "\n")

    def run(self, until: dt.datetime | None = None, poll_s: float = 60.0) -> None:
        done_days: set[dt.date] = set()
        watched: set[dt.date] = set()                    # sessions seen live before the hard flat time
        while until is None or dt.datetime.now(dt.timezone.utc) < until:
            now = pd.Timestamp.now(tz="UTC")
            day = now.tz_convert(NY).date()
            if not is_session(day) or day in done_days:
                time.sleep(300)
                continue
            w = window(day, self.cfg.session)
            t = now.tz_convert(NY)
            if t < w.open + pd.Timedelta(minutes=1):
                time.sleep(min(300.0, max(5.0, (w.open + pd.Timedelta(minutes=1, seconds=5) - t).total_seconds())))
                continue
            try:
                if t >= w.hard_flat + pd.Timedelta(minutes=2):
                    if day in watched:                   # never record a session replayed after the fact
                        self.step(now, final=True)
                    done_days.add(day)
                    continue
                self.step(now)
                watched.add(day)
            except Exception as e:                       # data failures: fail safe, log, retry next minute
                log.warning("paper step failed: %s: %s", type(e).__name__, e)
                print(json.dumps({"timestamp": t.isoformat(), "instrument": "SPY", "state": "NO_TRADE",
                                  "reason": f"data/engine error: {type(e).__name__}: {e}"}), flush=True)
            # wake 5 s after the next minute boundary
            nxt = (pd.Timestamp.now(tz="UTC").floor("min") + pd.Timedelta(minutes=1, seconds=5))
            time.sleep(max(1.0, (nxt - pd.Timestamp.now(tz="UTC")).total_seconds()))
