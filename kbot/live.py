"""Live paper trading: poll maxycharts.com, print one JSON decision per
direction per tick, and simulate fills. No real orders are ever placed."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .data import MaxyClient, _ts
from .memory import Memory
from .model import Calibrator, snapshot
from .strategy import Config, Engine, Position, taker_fee


def run(client: MaxyClient, cal: Calibrator, mem: Memory, cfg: Config,
        interval: float = 2.0, ticks: int | None = None, log_path: Path | None = None,
        quiet_waits: bool = False):
    eng = Engine(cal, mem, cfg)
    pos: Position | None = None
    market, n, realized = None, 0, 0.0
    fh = log_path.open("a") if log_path else None
    try:
        while ticks is None or n < ticks:
            start = time.monotonic()
            now = int(time.time())
            if market is None or _ts(market["close_time"]) <= now:
                if pos is not None:            # rolled over while holding: settle
                    settled = client._get(f"https://maxycharts.com/api/kalshi/markets/{market['ticker']}")["market"]
                    res = settled.get("result")
                    if res in ("yes", "no"):
                        won = (pos.side == "UP") == (res == "yes")
                        pnl = ((1.0 if won else 0.0) - pos.entry - taker_fee(pos.entry, pos.qty)) * pos.qty
                        realized += pnl
                        mem.record(pos.key, pnl / pos.qty, pos.entry, [] if pnl > 0 else ["thesis_failed: held to settlement and lost"])
                        _emit({"event": "SETTLED", "ticker": market["ticker"], "won": won,
                               "pnl_usd": round(pnl, 2), "realized_usd": round(realized, 2)}, fh)
                        pos = None
                    else:
                        time.sleep(interval)
                        continue
                market = client.current_market()
            close_ts = _ts(market["close_time"])
            strike = float(market["floor_strike"])
            index = client.index_series(market["event_ticker"], minutes=20)
            book = client.book(market["ticker"])
            s = snapshot(index, strike, close_ts, index[-1][0] if index else now, book.imbalance,
                         mid=(book.yes_bid + book.yes_ask) / 2 if book.yes_bid is not None and book.yes_ask is not None else None)
            if s is None:
                time.sleep(interval)
                continue
            for d in eng.decide(s, book, pos):
                d["contract_ticker"] = market["ticker"]
                if not (quiet_waits and d["market_state"].startswith("WAIT")):
                    _emit(d, fh)
                if pos is None and d["market_state"] == "ENTER_NOW":
                    pos = Position(d["direction"], d["execution_price"], cfg.qty, s.t,
                                   d["bucket"], d["confidence_score"],
                                   d["bucket"].split("|")[1] == "aligned")
                    _emit({"event": "PAPER_FILL", "side": pos.side, "price": pos.entry, "qty": pos.qty}, fh)
                    break
                if pos is not None and d["market_state"] == "EXIT_NOW":
                    px = d["execution_price"]
                    pnl = (px - pos.entry - taker_fee(pos.entry, pos.qty) - taker_fee(px, pos.qty)) * pos.qty
                    realized += pnl
                    mem.record(pos.key, pnl / pos.qty, pos.entry, [])
                    _emit({"event": "PAPER_EXIT", "price": px, "pnl_usd": round(pnl, 2),
                           "realized_usd": round(realized, 2)}, fh)
                    pos = None
                    break
            n += 1
            time.sleep(max(interval - (time.monotonic() - start), 0))
    finally:
        if fh:
            fh.close()
    return realized


def _emit(d: dict, fh):
    line = json.dumps(d)
    print(line, flush=True)
    if fh:
        fh.write(line + "\n")
