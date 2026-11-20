"""Live paper trading with the day-trader model (kbot/trader.py).

Once a minute, right after the minute closes, it:
  1. pulls the BRTI index, the Kalshi book and quotes (MaxyCharts) and
     Coinbase minutes, and builds exactly the features the backtest used
  2. checks open positions: sells at the bid (minus slippage) once the bid
     is at +take-profit or -stop-loss, or one minute before close
  3. if no entry in the last 5 minutes, asks the model for the predicted P&L
     of each side and each TP/SL pair, and buys the best one if it clears
     the margin
  4. scores the candle / chart patterns that printed (pattern_memory.py)
  5. prints one JSON decision and appends it to reports/paper.jsonl

Exits are monitor-and-sell-at-bid, the same rule the backtest simulated:
no resting orders. It never places real orders.

  python -m kbot.trader --days 70 --drop flow,calendar --save-model state/trader-model.pkl --no-walk
  python -m kbot paper --margin 0.02
"""
from __future__ import annotations

import json
import math
import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .data import MaxyClient, Window, _ts, KALSHI
from .pattern_memory import PatternMemory
from .patterns import Series, vol_per_sec
from .strategy import taker_fee
from .sweep import load_cached
from .trader import Context, features

SLIP = 0.01


@dataclass
class Pos:
    side: str                     # "up" / "dn"
    tp: float
    sl: float
    entry: float                  # ask + slippage
    qty: int
    t: int
    ticker: str
    patterns: list = field(default_factory=list)
    predicted: float = 0.0


def _bid(side, yb, ya):
    return yb if side == "up" else round(1 - ya, 4)


def _ask(side, yb, ya):
    return ya if side == "up" else round(1 - yb, 4)


def _emit(d, fh):
    line = json.dumps(d)
    print(line, flush=True)
    if fh:
        fh.write(line + "\n")
        fh.flush()


class Paper:
    def __init__(self, model_path="state/trader-model.pkl", margin=0.02, qty=10, days=35,
                 state=Path("state"), reports=Path("reports"), rate=0.3, retrain_hour=6):
        with open(model_path, "rb") as fh:
            self.m = pickle.load(fh)
        self.names = self.m["names"]
        self.margin, self.qty = margin, qty
        self.client = MaxyClient("BTC", min_interval=rate)
        self.reports = reports
        reports.mkdir(exist_ok=True)
        self.state = state
        self.mem = PatternMemory(state / "pattern-memory.json")
        self.positions: list[Pos] = []
        self.last_entry = {}
        self.showing: dict[str, list] = {}
        self.closed: list[dict] = []
        sp = state / "paper-summary.json"
        if sp.exists():
            saved = json.loads(sp.read_text())
            self.closed = saved.get("trades", [])
            # positions that were open when the last run stopped
            self.positions = [Pos(**d) for d in saved.get("open", [])]
            for p in self.positions:
                self.last_entry[p.ticker] = max(self.last_entry.get(p.ticker, 0), p.t)
        self.model_path = Path(model_path)
        self.retrain_hour = retrain_hour
        self._retrain = None                   # running retrain subprocess
        self._retrained_on = None
        self._boot(days)

    # -- startup: history the features need (session VWAP, swing levels, hour tables)
    def _boot(self, days):
        ws = load_cached("BTC", days)
        last = max((w.close_ts for w in ws), default=int(time.time()) - 86400)
        now = int(time.time())
        if now - last > 1800:
            print(f"filling {int((now - last) / 900)} windows since the cache ends...", flush=True)
            for m in self.client.settled_markets(last + 1, now):
                try:
                    w = self.client.window(m)
                except Exception:
                    w = None
                if w:
                    ws.append(w)
        ws.sort(key=lambda w: w.close_ts)
        self.series = Series(ws)
        self.ctx = Context(ws, self.series)
        print(f"ready: {len(ws)} windows of history, model trained on {self.m['trained_windows']} windows "
              f"at {datetime.fromtimestamp(self.m['trained_at'], timezone.utc):%Y-%m-%d %H:%M}Z, "
              f"skills off: {self.m['drop'] or 'none'}", flush=True)

    def _quotes(self, mk, T, book):
        cs = self.client._get(f"{KALSHI}/series/{self.client.series}/markets/{mk['ticker']}/candlesticks",
                              {"start_ts": _ts(mk["open_time"]), "end_ts": T, "period_interval": 1})
        q = []
        for c in cs.get("candlesticks") or []:
            t = int(c["end_period_ts"])
            yb = c.get("yes_bid") or {}
            ya = c.get("yes_ask") or {}
            b = yb.get("close_dollars", yb.get("close"))
            a = ya.get("close_dollars", ya.get("close"))
            if t < T and b is not None and a is not None:
                b, a = float(b), float(a)
                if 0.005 < b <= a < 0.995:
                    q.append((t, b, a))
        q.append((T, book.yes_bid, book.yes_ask))
        return q

    def _settle_leftovers(self, mk, fh):
        """Positions still open after a market closed: settle at the result."""
        left = [p for p in self.positions if p.ticker == mk["ticker"]]
        if not left:
            return True
        res = self.client.market(mk["ticker"]) or {}
        if res.get("result") not in ("yes", "no"):
            return False
        for p in left:
            won = (p.side == "up") == (res["result"] == "yes")
            pnl = (1.0 if won else 0.0) - p.entry - taker_fee(min(p.entry, 0.99), p.qty)
            self._close(p, None, pnl, "settled", fh)
        return True

    def _close(self, p, px, pnl_c, why, fh):
        self.positions.remove(p)
        self.mem.record_trade(p.patterns, pnl_c)
        rec = {"event": "PAPER_EXIT", "ticker": p.ticker, "side": "UP" if p.side == "up" else "DOWN",
               "entry": p.entry, "exit": px, "why": why, "tp": p.tp, "sl": p.sl,
               "pnl_usd": round(pnl_c * p.qty, 2), "held_s": int(time.time()) - p.t,
               "patterns_at_entry": p.patterns[:8], "predicted_pnl_c": round(p.predicted, 4)}
        self.closed.append(rec)
        rec["realized_usd"] = round(sum(r["pnl_usd"] for r in self.closed), 2)
        _emit(rec, fh)
        with open(self.reports / "paper_trades.jsonl", "a") as th:
            th.write(json.dumps(rec) + "\n")

    def _summary(self):
        pn = [r["pnl_usd"] for r in self.closed]
        d = {"trades": self.closed, "n": len(pn), "pnl_usd": round(sum(pn), 2),
             "win_rate": round(float(np.mean(np.array(pn) > 0)), 3) if pn else None,
             "open": [p.__dict__ for p in self.positions]}
        (self.state / "paper-summary.json").write_text(json.dumps(d, indent=1))
        return d

    def _mistake_note(self):
        losses = [r for r in self.closed[-20:] if r["pnl_usd"] < 0]
        if not losses:
            return "no recent losses"
        r = losses[-1]
        bad = []
        for k in r.get("patterns_at_entry", []):
            v = self.mem.view(k).get("trades")
            if v and v["n"] >= 5 and v["avg_pnl_per_contract"] < 0:
                bad.append(f"{k} ({v['n']} trades, {v['avg_pnl_per_contract']:+.3f}/contract)")
        note = f"last loss {r['side']} {r['ticker']} {r['why']} {r['pnl_usd']:+.2f}"
        return note + ("; patterns losing money when present: " + ", ".join(bad) if bad else "")

    # -- one decision per minute
    def step(self, mk, T, fh):
        c = self.client
        pts = c.index_series(mk["event_ticker"], minutes=20)
        self.series.extend(pts)
        last_idx = max((t for t, _ in pts), default=0)
        t = min(T, last_idx)
        if T - t > 15:
            _emit({"timestamp": T, "market_state": "WAIT_FOR_BETTER_ENTRY", "rationale": "index feed stale"}, fh)
            return
        try:
            self.ctx.refresh(T)
        except Exception as e:                     # Coinbase hiccup: features use what we have
            print(f"coinbase refresh failed: {e}", flush=True)
        book = c.book(mk["ticker"])
        if book.yes_bid is None or book.yes_ask is None:
            return
        close_ts, open_ts = _ts(mk["close_time"]), _ts(mk["open_time"])
        w = Window(event=mk["event_ticker"], ticker=mk["ticker"], strike=float(mk["floor_strike"]),
                   open_ts=open_ts, close_ts=close_ts)
        sig = vol_per_sec(self.series.upto(t, 1800))
        seen = self.mem.observe(self.series, t - t % 60, sig)
        self.mem.resolve(self.series, t)
        # patterns on the latest completed candle of each timeframe stay "showing"
        # until that timeframe's next candle closes
        for tf in ("1m", "5m", "15m"):
            new_tf = [k for k in seen if k.startswith(tf + ":")]
            sec = {"1m": 60, "5m": 300, "15m": 900}[tf]
            if new_tf or (t - t % 60) % sec == 0:
                self.showing[tf] = new_tf
        active = [k for tf in ("15m", "5m", "1m") for k in self.showing.get(tf, [])]

        # exits first
        for p in [p for p in self.positions if p.ticker == mk["ticker"]]:
            b = _bid(p.side, book.yes_bid, book.yes_ask) - SLIP
            why = ("take_profit" if b >= p.entry + p.tp else "stop_loss" if b <= p.entry - p.sl
                   else "before_close" if T >= close_ts - 60 else None)
            if why:
                pnl = b - p.entry - taker_fee(min(p.entry, 0.99), p.qty) - taker_fee(max(b, 0.01), p.qty)
                self._close(p, round(b, 4), pnl, why, fh)
                self._decision(fh, T, mk, "EXIT_NOW", p.side, None, b, p, why, active)
            else:
                self._decision(fh, T, mk, "WAIT_FOR_BETTER_EXIT", p.side, None, b, p, "holding", active)

        q = self._quotes(mk, T, book)
        f = features(self.ctx, w, t, q, None) if len(q) >= 4 else None
        if f is None:
            if not self.positions:
                self._decision(fh, T, mk, "WAIT_FOR_BETTER_ENTRY", None, None, None, None,
                               "not enough quote history in this window yet", active)
            return
        x = np.array([[f.get(n, np.nan) for n in self.names]], dtype=float)
        pred = {k: float(mdl.predict(x)[0]) for k, mdl in self.m["models"].items()}
        p_up = float(self.m["idx_clf"].predict_proba(x)[0, 1])
        best_k, best_v = max(pred.items(), key=lambda kv: kv[1])
        side, tp, sl = best_k.split("_")
        tp, sl = float(tp), float(sl)
        can_enter = (T <= close_ts - 120 and T > self.last_entry.get(mk["ticker"], 0) + 300)
        info = {"pred": pred, "p_up": p_up, "f": f, "best": (best_k, best_v)}
        if can_enter and best_v > self.margin:
            a = _ask(side, book.yes_bid, book.yes_ask) + SLIP
            p = Pos(side, tp, sl, round(a, 4), self.qty, T, mk["ticker"], list(active), best_v)
            self.positions.append(p)
            self.last_entry[mk["ticker"]] = T
            self._decision(fh, T, mk, "ENTER_NOW", side, info, a, p, "edge above margin", active)
            _emit({"event": "PAPER_FILL", "side": "UP" if side == "up" else "DOWN", "price": p.entry,
                   "qty": p.qty, "take_profit_bid": round(p.entry + tp, 4),
                   "stop_loss_bid": round(max(p.entry - sl, 0.01), 4)}, fh)
        elif not [p for p in self.positions if p.ticker == mk["ticker"]]:
            why = ("no entries in the last 2 minutes" if T > close_ts - 120 else
                   "waiting 5 min after last entry" if not can_enter else
                   f"best predicted edge {best_v * 100:+.1f}c/contract is under the {self.margin * 100:.0f}c margin")
            self._decision(fh, T, mk, "WAIT_FOR_BETTER_ENTRY", side, info, None, None, why, active)

    def _decision(self, fh, T, mk, state, side, info, px, pos, why, patterns):
        d = {"timestamp": datetime.fromtimestamp(T, timezone.utc).isoformat(), "contract_ticker": mk["ticker"],
             "market_state": state,
             "direction": None if side is None else ("UP" if side == "up" else "DOWN")}
        if info:
            pu = info["p_up"]
            conf = pu if side == "up" else 1 - pu
            d["confidence_score"] = round(100 * conf, 1)
            f = info["f"]
            d["index_call"] = {"p_target_before_stop_up": round(pu, 3),
                               "move_used": round(0.5 * f.get("vol__typical_15m_move", float("nan")), 2)}
            k, v = info["best"]
            d["best_setup"] = {"pair": k, "predicted_pnl_per_contract": round(v, 4)}
        if pos:
            d["predicted_trough_or_peak"] = {"take_profit_bid": round(pos.entry + pos.tp, 4),
                                             "stop_loss_bid": round(max(pos.entry - pos.sl, 0.01), 4),
                                             "entry": pos.entry}
        d["execution_price"] = None if px is None else round(px, 4)
        summ = self._summary()
        d["simulation_metrics"] = {"paper_trades": summ["n"], "paper_win_rate": summ["win_rate"],
                                   "paper_pnl_usd": summ["pnl_usd"],
                                   "recent_mistake_correction": self._mistake_note()}
        views = [self.mem.view(k) for k in patterns]
        lean = self.mem.lean(patterns)
        d["patterns"] = {"showing": [v["pattern"] for v in views][:12],
                         "reliable_lean_sigma": round(lean, 3),
                         "warnings": [f"{v['pattern']}: {v['warning']}" for v in views if "warning" in v]}
        r = [why]
        if info:
            f = info["f"]
            lab = {0: "chop", 1: "range", 2: "trend"}.get(int(f.get("regime__label", -1)) if f.get("regime__label") == f.get("regime__label") else -1, "?")
            r.append(f"regime {lab}")
            vw = f.get("price__vs_session_vwap")
            if vw is not None and vw == vw:
                r.append(f"{'above' if vw > 0 else 'below'} session VWAP")
            r.append(f"P(index up half a move first) {info['p_up']:.0%}")
        if abs(lean) >= 0.1:
            r.append(f"reliable patterns lean {'up' if lean > 0 else 'down'} ({lean:+.2f} sigma)")
        d["rationale"] = "; ".join(r)
        _emit(d, fh)

    # -- learning from new markets ------------------------------------------------
    def _cache_recent(self):
        """Save the markets that settled in the last hour, so retraining sees them."""
        now = int(time.time())
        for m in self.client.settled_markets(now - 3600, now):
            try:
                self.client.window(m)
            except Exception:
                pass

    def _maybe_retrain(self, fh):
        """Once a day (at retrain_hour UTC) retrain on everything cached, in the
        background, and switch to the new model when it is ready."""
        import os
        import subprocess
        import sys
        if self._retrain is not None:
            rc = self._retrain.poll()
            if rc is None:
                return
            new = self.model_path.with_suffix(".new.pkl")
            if rc == 0 and new.exists():
                os.replace(new, self.model_path)
                with open(self.model_path, "rb") as fh2:
                    self.m = pickle.load(fh2)
                self.names = self.m["names"]
                _emit({"event": "RETRAINED", "trained_windows": self.m["trained_windows"]}, fh)
            else:
                _emit({"event": "RETRAIN_FAILED", "returncode": rc}, fh)
            self._retrain = None
            return
        if self.retrain_hour is None:
            return
        now = datetime.now(timezone.utc)
        if now.hour != self.retrain_hour or self._retrained_on == now.date():
            return
        self._retrained_on = now.date()
        cmd = [sys.executable, "-m", "kbot.trader", "--days", "70", "--no-walk",
               "--save-model", str(self.model_path.with_suffix(".new.pkl"))]
        if self.m.get("drop"):
            cmd += ["--drop", ",".join(self.m["drop"])]
        if self.m.get("lean_candles"):
            cmd.append("--lean-candles")
        log = open(self.state / "retrain.log", "a")
        self._retrain = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        _emit({"event": "RETRAIN_STARTED"}, fh)

    def run(self, minutes=None):
        fh = open(self.reports / "paper.jsonl", "a")
        mk, n = None, 0
        try:
            while minutes is None or n < minutes:
                now = time.time()
                T = int(now) - int(now) % 60 + 60
                time.sleep(max(T + 3 - time.time(), 0))     # let the minute's data land
                try:
                    if mk is None or _ts(mk["close_time"]) <= T:
                        if mk is not None and not self._settle_leftovers(mk, fh):
                            pass                              # try again next minute
                        mk = self.client.current_market()
                        try:
                            self._cache_recent()
                        except Exception:
                            pass
                    self._maybe_retrain(fh)
                    self.step(mk, T, fh)
                    for old in {p.ticker for p in self.positions} - {mk["ticker"]}:
                        self._settle_leftovers({"ticker": old}, fh)
                except Exception as e:
                    print(json.dumps({"event": "ERROR", "error": repr(e)[:300]}), flush=True)
                self.mem.save()
                n += 1
        finally:
            fh.close()
            self.mem.save()
            self._summary()


def report(state=Path("state")):
    """Paper results so far: totals, by price bucket, by setup, by pattern."""
    sp = state / "paper-summary.json"
    if not sp.exists():
        print("no paper trades yet")
        return
    d = json.loads(sp.read_text())
    tr = d["trades"]
    pn = np.array([r["pnl_usd"] for r in tr]) if tr else np.array([])
    print(f"PAPER TRADES: {len(tr)}  P&L ${pn.sum():.2f}  win {np.mean(pn > 0) if len(pn) else 0:.1%}  "
          f"open now: {len(d.get('open', []))}")
    if not tr:
        return
    def table(title, keyf):
        g = {}
        for r in tr:
            for k in keyf(r):
                g.setdefault(k, []).append(r["pnl_usd"])
        print(f"\n{title}")
        for k in sorted(g, key=lambda k: -sum(g[k])):
            v = np.array(g[k])
            print(f"  {k:34} {len(v):4d} trades  win {np.mean(v > 0):5.1%}  P&L ${v.sum():8.2f}")
    bucket = lambda r: [f"entry {int(r['entry'] * 10) * 10:2d}-{int(r['entry'] * 10) * 10 + 9}c"]
    table("BY ENTRY PRICE", bucket)
    table("BY EXIT REASON", lambda r: [r["why"]])
    table("BY SIDE AND SETUP", lambda r: [f"{r['side']} tp{r['tp']} sl{r['sl']}"])
    table("BY PATTERN SHOWING AT ENTRY", lambda r: r.get("patterns_at_entry", []))
    pred = np.array([r.get("predicted_pnl_c", 0) for r in tr])
    real = pn / 10
    print(f"\npredicted {pred.mean() * 100:+.2f}c/contract vs actual {real.mean() * 100:+.2f}c/contract")
    mem = PatternMemory(state / "pattern-memory.json")
    sb = mem.scoreboard(min_n=20)
    if sb:
        print("\nPATTERNS SCORED LIVE (move over the next 5-15 min, in sigma)")
        for v in sb[:25]:
            h = v.get("history", {})
            print(f"  {v['pattern']:32} live n={v['live']['n']:5d} avg {v['live']['avg_move_sigma']:+.3f}  "
                  f"history avg {h.get('avg_move_sigma', float('nan')):+.3f} reliable={h.get('reliable')}"
                  f"{'  ' + v['warning'] if 'warning' in v else ''}")
