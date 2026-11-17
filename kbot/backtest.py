"""Walk-forward backtest over settled 15-minute windows.

For each window, oldest first, the engine decides at every 1-minute Kalshi
quote using only the index data printed up to that moment. Buys fill at the
ask plus slippage and sells at the bid minus slippage, with Kalshi taker fees
on both sides. Anything still held at the close settles at $1 or $0. After
each window the calibrator learns from the outcome and the memory gets a
post-mortem, so every decision uses only what came before it.

Limitation: historical depth isn't available (only the 1-minute top-of-book
close), so fills assume our size fits at the touch and the book-imbalance
feature is 0 in backtests.
"""
from __future__ import annotations

import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .data import Book, MaxyClient, Window, _ts as _dt
from .memory import Memory
from .model import Calibrator, snapshot
from .strategy import Config, Engine, Position, taker_fee


@dataclass
class Trade:
    ticker: str
    side: str
    entry_t: int
    entry: float
    exit_t: int
    exit: float
    how: str
    pnl: float              # $ total for qty
    qty: int
    p_entry: float
    key: str
    won_market: bool


def fetch_windows(client: MaxyClient, days: float, end_ts: int | None = None,
                  workers: int = 4, log=print) -> list[Window]:
    end_ts = end_ts or int(time.time()) - 60
    start_ts = end_ts - int(days * 86400)
    log(f"listing settled {client.series} markets for the last {days:g} days...")
    markets = client.settled_markets(start_ts, end_ts)
    if markets:
        oldest = min(int(_dt(m["close_time"])) for m in markets)
        if oldest - 900 > start_ts:
            older = client.older_markets(start_ts, oldest - 900)
            log(f"listing reaches back to {time.strftime('%Y-%m-%d', time.gmtime(oldest))}; "
                f"adding {len(older)} older windows by ticker")
            markets += older
    log(f"{len(markets)} markets; downloading history (cached in {client.cache})")
    out, done = [], 0
    with ThreadPoolExecutor(workers) as ex:
        for w in ex.map(_safe(client), markets):
            done += 1
            if w:
                out.append(w)
            if done % 100 == 0:
                log(f"  {done}/{len(markets)}")
    out.sort(key=lambda w: w.close_ts)
    return out


def _safe(client):
    def f(m):
        try:
            return client.window(m)
        except Exception as e:  # one bad window shouldn't kill a year-long run
            print(f"  skip {m.get('ticker')}: {e}")
            return None
    return f


def run(windows: list[Window], cal: Calibrator, mem: Memory, cfg: Config,
        slippage: float = 0.01, decisions_path: Path | None = None) -> dict:
    eng = Engine(cal, mem, cfg)
    trades: list[Trade] = []
    brier_model = brier_market = brier_prior = 0.0
    n_pts = 0
    calib = [[0, 0.0, 0] for _ in range(10)]      # per P(UP) decile: n, sum p, wins
    dec_fh = decisions_path.open("w") if decisions_path else None

    for w in windows:
        if w.result not in ("yes", "no") or not w.index:
            continue
        up_won = w.result == "yes"
        pos: Position | None = None
        snaps, path = [], []          # path: (t, book)
        traded = False
        for (t, yb, ya, vol) in w.quotes:
            if yb is None or ya is None or ya >= 0.995 or yb <= 0.005 or ya <= yb - 1e-9:
                continue
            if t >= w.close_ts:
                continue
            book = Book(yes_bid=yb, yes_ask=ya)
            s = snapshot(w.index, w.strike, w.close_ts, t, mid=(yb + ya) / 2)
            if s is None:
                continue
            snaps.append(s)
            path.append((t, book))
            for d in eng.decide(s, book, pos):
                if dec_fh and d["market_state"] in ("ENTER_NOW", "EXIT_NOW"):
                    dec_fh.write(json.dumps(d) + "\n")
                if pos is None and not traded and d["market_state"] == "ENTER_NOW":
                    side = d["direction"]
                    fill = min(book.ask(side) + slippage, 0.99)
                    pos = Position(side=side, entry=fill, qty=cfg.qty, t=t, key=d["bucket"],
                                   p_entry=d["confidence_score"],
                                   aligned=d["bucket"].split("|")[1] == "aligned")
                    traded = True
                    break
                if pos is not None and d["market_state"] == "EXIT_NOW":
                    fill = max(book.bid(pos.side) - slippage, 0.0)
                    trades.append(_close(w, pos, t, fill, "sold", up_won))
                    pos = None
                    break
        if pos is not None:
            won = (pos.side == "UP") == up_won
            trades.append(_close(w, pos, w.close_ts, 1.0 if won else 0.0, "settled", up_won))
            pos = None

        # ---- learning: score, then update (walk-forward) -----------------
        for s, (t, book) in zip(snaps, path):
            pm = cal.p_up(s)
            mid = (book.yes_bid + book.yes_ask) / 2
            y = 1.0 if up_won else 0.0
            brier_model += (pm - y) ** 2
            brier_market += (mid - y) ** 2
            brier_prior += (s.p0 - y) ** 2
            n_pts += 1
            c = calib[min(int(pm * 10), 9)]
            c[0] += 1; c[1] += pm; c[2] += up_won
        for s in snaps:
            cal.update(s, up_won)
        if traded:
            _post_mortem(trades[-1], path, mem)
        else:
            _missed(w, snaps, path, up_won, cal, mem)

    if dec_fh:
        dec_fh.close()
    return summarize(trades, mem, cal, n_pts, brier_model, brier_market, brier_prior, calib, len(windows))


def _close(w: Window, pos: Position, t: int, px: float, how: str, up_won: bool) -> Trade:
    fees = taker_fee(pos.entry, pos.qty) + (taker_fee(px, pos.qty) if how == "sold" else 0.0)
    pnl = (px - pos.entry - fees) * pos.qty
    return Trade(w.ticker, pos.side, pos.t, pos.entry, t, px, how, round(pnl, 4), pos.qty,
                 pos.p_entry, pos.key, (pos.side == "UP") == up_won)


def _post_mortem(tr: Trade, path, mem: Memory):
    side = tr.side
    after_entry = [(t, b) for t, b in path if tr.entry_t < t <= tr.exit_t]
    after_exit = [(t, b) for t, b in path if t > tr.exit_t]
    notes = []
    asks = [b.ask(side) for _, b in after_entry if b.ask(side) is not None]
    if asks and min(asks) <= tr.entry - 0.03:
        notes.append(f"premature_entry: ask fell to {min(asks):.2f} after buying at {tr.entry:.2f}")
    bids = [b.bid(side) for _, b in after_exit if b.bid(side) is not None]
    if tr.how == "sold" and bids and max(bids) >= tr.exit + 0.03:
        notes.append(f"early_exit: bid reached {max(bids):.2f} after selling at {tr.exit:.2f}")
    if tr.pnl < 0:
        aligned = tr.key.split("|")[1] == "aligned"
        if tr.how == "sold" and tr.won_market:
            notes.append("shaken_out: stopped out, but this side went on to win")
        elif aligned:
            notes.append(f"false_breakout: momentum agreed at entry (P {tr.p_entry:.0%}) then reversed")
        else:
            notes.append(f"thesis_failed: P {tr.p_entry:.0%} at entry, side lost")
    mem.record(tr.key, tr.pnl / tr.qty, tr.entry, notes)


def _missed(w, snaps, path, up_won, cal, mem: Memory):
    side = "UP" if up_won else "DOWN"
    for i, (t, b) in enumerate(path):
        a = b.ask(side)
        later = [bb.bid(side) for _, bb in path[i + 1:] if bb.bid(side) is not None]
        if a is not None and a <= 0.90 and later and max(later) >= a + 0.06:
            p = cal.p_up(snaps[i]) if side == "UP" else 1 - cal.p_up(snaps[i])
            mem.missed += 1
            if mem.missed % 25 == 1:
                mem.lessons.append(f"missed: {w.ticker} {side} ask {a:.2f} later bid {max(later):.2f}; "
                                   f"model only gave {p:.0%}")
            return


def summarize(trades, mem, cal, n_pts, bm, bk, bp, calib, n_windows) -> dict:
    pnl = [t.pnl for t in trades]
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in pnl:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    cost = sum(t.entry * t.qty for t in trades)
    wins = sum(1 for x in pnl if x > 0)
    by_exit = {}
    for t in trades:
        e = by_exit.setdefault(t.how, {"n": 0, "pnl": 0.0, "wins": 0})
        e["n"] += 1; e["pnl"] = round(e["pnl"] + t.pnl, 2); e["wins"] += t.pnl > 0
    return {
        "windows": n_windows,
        "trades": len(trades),
        "win_rate": round(wins / len(trades), 4) if trades else None,
        "total_pnl_usd": round(sum(pnl), 2),
        "avg_pnl_per_trade_usd": round(sum(pnl) / len(trades), 4) if trades else None,
        "roi_on_capital_deployed": round(sum(pnl) / cost, 4) if cost else None,
        "max_drawdown_usd": round(dd, 2),
        "avg_entry_price": round(sum(t.entry for t in trades) / len(trades), 4) if trades else None,
        "avg_predicted_p": round(sum(t.p_entry for t in trades) / len(trades), 4) if trades else None,
        "by_exit": by_exit,
        "brier": {"model": round(bm / n_pts, 5) if n_pts else None,
                  "physics_prior": round(bp / n_pts, 5) if n_pts else None,
                  "market_mid": round(bk / n_pts, 5) if n_pts else None,
                  "note": "lower is better; model must beat market_mid to have a real edge"},
        "calibration": [{"p_bucket": f"{i/10:.1f}-{(i+1)/10:.1f}", "n": c[0],
                         "avg_p": round(c[1] / c[0], 3), "actual": round(c[2] / c[0], 3)}
                        for i, c in enumerate(calib) if c[0]],
        "mistakes": dict(mem.mistakes),
        "missed_opportunities": mem.missed,
        "bucket_thresholds": {k: mem.threshold(k) for k in sorted(mem.adjust)},
        "calibrator": {"a_physics": round(cal.a, 3), "c_market": round(cal.c, 3), "b": round(cal.b, 3),
                       "w": {k: round(v, 3) for k, v in cal.w.items()}, "updates": cal.n},
        "_trades": trades,
    }


def write_reports(summary: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    trades = summary.pop("_trades")
    with (out_dir / f"trades-{stamp}.csv").open("w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(Trade.__dataclass_fields__.keys())
        for t in trades:
            wr.writerow(t.__dict__.values())
    p = out_dir / f"backtest-{stamp}.json"
    p.write_text(json.dumps(summary, indent=2))
    return p
