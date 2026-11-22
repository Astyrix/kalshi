"""Live market-making paper trader: does resting limit orders (maker) make money?

The taker bot found no edge after the spread and fees. The one idea left is
to be the maker: rest bids, get filled by takers, collect the spread. Whether
that works depends on two things a backtest cannot see, so this measures them
live on the real KXBTC15M book (paper only, no orders are sent):

  queue    our virtual order joins the back of the line at its price; it only
           fills after the size that was ahead of us has traded (or right away
           if a trade prints through our price).
  adverse  fills tend to come when the price is about to move against us.

Every ~2.5 s it reads the book and the new trades. It rests a YES bid and a NO
bid (a YES+NO pair is worth exactly $1, so filling both locks in the spread).
If the spread is >= 2c it improves the bid by 1c (front of a new queue);
otherwise it joins the best bid. Net inventory is capped, quoting stops a few
minutes before close, and anything held settles at $1/$0.

Two queue models are scored side by side:
  strict   only trades at our price move us up the line
  loose    the line also shrinks when resting size at our price is cancelled

  python -m kbot.maker_paper --minutes 115
  python -m kbot.maker_paper --report
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from .data import KALSHI, MaxyClient, _ts

OUT = Path("reports/maker_paper.jsonl")
STATE = Path("state/maker-paper.json")
LATENCY = 0.5      # seconds from deciding to the order resting on the book


def maker_fee(p, qty, rate=0.0175):
    return math.ceil(rate * qty * p * (1 - p) * 100 - 1e-9) / 100


def levels(client, ticker, depth=10):
    d = client._get(f"{KALSHI}/markets/{ticker}/orderbook", {"depth": depth})["orderbook_fp"]
    # prices can be sub-cent near the extremes (e.g. 0.995): keep 4 decimals, not 2
    yes = {round(float(p), 4): float(q) for p, q in d.get("yes_dollars") or []}
    no = {round(float(p), 4): float(q) for p, q in d.get("no_dollars") or []}
    return yes, no


class Order:
    """One virtual resting bid on a side ('yes' or 'no') at price px."""

    def __init__(self, side, px, ahead, qty):
        self.side, self.px, self.qty = side, px, qty
        self.ahead = {"strict": ahead, "loose": ahead}
        self.filled = {"strict": 0.0, "loose": 0.0}
        self.t = time.time()

    def on_trade(self, side_px, vol):
        """A taker sold into bids on our side at side_px for vol contracts."""
        for m in self.ahead:
            left = self.qty - self.filled[m]
            if left <= 0:
                continue
            if side_px < self.px - 1e-9:          # printed through us: we were filled first
                self.filled[m] = self.qty
                continue
            if abs(side_px - self.px) > 1e-9:
                continue
            take = max(0.0, vol - self.ahead[m])
            self.ahead[m] = max(0.0, self.ahead[m] - vol)
            self.filled[m] += min(left, take)

    def on_book(self, size_at_px):
        # loose: cancels ahead of us also move us up
        self.ahead["loose"] = min(self.ahead["loose"], size_at_px)


class Maker:
    def __init__(self, qty=10, max_inv=30, stop_min=3.0, poll=2.5):
        self.c = MaxyClient("BTC", min_interval=0.3)
        self.qty, self.max_inv, self.stop_s, self.poll = qty, max_inv, stop_min * 60, poll

    # ---- one market --------------------------------------------------------
    def run_market(self, mk, until, fh):
        tk, close = mk["ticker"], _ts(mk["close_time"])
        st = self._load(tk)
        fills = st["fills"]          # model -> list of [side, px, qty, ts]
        orders = {}                  # side -> Order
        seen, last_ts = set(st.get("seen", [])), st.get("last_ts", int(time.time()) - 5)
        quotes = 0
        while time.time() < min(close, until):
            try:
                yes, no = levels(self.c, tk)
                tr = self.c._get(f"{KALSHI}/markets/trades",
                                 {"ticker": tk, "min_ts": last_ts - 1, "limit": 1000}).get("trades") or []
            except Exception:
                time.sleep(self.poll)
                continue
            # trades oldest first
            for x in reversed(tr):
                if x["trade_id"] in seen:
                    continue
                seen.add(x["trade_id"])
                last_ts = max(last_ts, _ts(x["created_time"]))
                vol = float(x["count_fp"])
                # taker bought NO -> sold into YES bids at yes_price; taker bought YES -> into NO bids
                hit = "yes" if x["taker_side"] == "no" else "no"
                px = float(x["yes_price_dollars"] if hit == "yes" else x["no_price_dollars"])
                o = orders.get(hit)
                tt = datetime.fromisoformat(x["created_time"].replace("Z", "+00:00")).timestamp()
                if o and tt >= o.t + LATENCY:          # only trades after our order could reach the book
                    before = dict(o.filled)
                    o.on_trade(px, vol)
                    for m in o.filled:
                        got = o.filled[m] - before[m]
                        if got > 1e-9:
                            fills[m].append([hit, o.px, got, int(time.time())])
            for side, o in list(orders.items()):
                o.on_book((yes if side == "yes" else no).get(o.px, 0.0))
                if all(o.filled[m] >= o.qty - 1e-9 for m in o.filled):
                    del orders[side]
            # requote
            left = close - time.time()
            by = max(yes, default=None)
            bn = max(no, default=None)
            if by is None or bn is None or left < self.stop_s:
                orders.clear()
            else:
                spread = round(1 - bn - by, 2)       # yes ask - yes bid
                for side, best, book in (("yes", by, yes), ("no", bn, no)):
                    inv = self._inv(fills["strict"])
                    if (side == "yes" and inv >= self.max_inv) or (side == "no" and inv <= -self.max_inv):
                        orders.pop(side, None)
                        continue
                    want = round(best + 0.01, 2) if spread >= 0.02 else best
                    if want <= 0.01 or want >= 0.99:
                        orders.pop(side, None)
                        continue
                    o = orders.get(side)
                    # keep our place unless the market moved a cent+ away or we'd now be crossing
                    if o and (want - o.px) < 0.01 - 1e-9 and o.px <= want + 1e-9:
                        continue
                    ahead = 0.0 if want > best else book.get(want, 0.0)
                    orders[side] = Order(side, want, ahead, self.qty)
                    quotes += 1
            self._save(tk, fills, seen, last_ts)
            time.sleep(self.poll)
        if time.time() < close:
            return False                        # stopped early (time budget); resume next run
        # settle
        res = None
        for _ in range(40):
            m = self.c.market(tk)
            if m and m.get("result") in ("yes", "no"):
                res = m["result"]
                break
            time.sleep(15)
        if res is None:
            return True
        row = {"ticker": tk, "result": res, "ts": datetime.now(timezone.utc).isoformat(), "requotes": quotes}
        for m, fl in fills.items():
            gross = sum(q * ((1.0 if s == res else 0.0) - px) for s, px, q, _ in fl)
            fee = sum(maker_fee(px, q) for s, px, q, _ in fl)
            ny = sum(q for s, px, q, _ in fl if s == "yes")
            nn = sum(q for s, px, q, _ in fl if s == "no")
            row[m] = {"yes_filled": round(ny, 2), "no_filled": round(nn, 2), "pairs": round(min(ny, nn), 2),
                      "pnl_no_fee": round(gross, 3), "pnl": round(gross - fee, 3)}
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        print(json.dumps(row), flush=True)
        STATE.unlink(missing_ok=True)
        return True

    @staticmethod
    def _inv(fl):
        return sum(q if s == "yes" else -q for s, px, q, _ in fl)

    def _load(self, tk):
        try:
            s = json.loads(STATE.read_text())
            if s.get("ticker") == tk:
                return s
        except Exception:
            pass
        return {"fills": {"strict": [], "loose": []}}

    def _save(self, tk, fills, seen, last_ts):
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps({"ticker": tk, "fills": fills, "last_ts": last_ts,
                                     "seen": list(seen)[-3000:]}))

    def run(self, minutes):
        until = time.time() + minutes * 60
        OUT.parent.mkdir(exist_ok=True)
        done = set()
        if OUT.exists():
            done = {json.loads(l)["ticker"] for l in OUT.read_text().splitlines() if l.strip()}
        with open(OUT, "a") as fh:
            while time.time() < until:
                try:
                    mk = self.c.current_market()
                except Exception:
                    time.sleep(10)
                    continue
                if mk["ticker"] in done or _ts(mk["close_time"]) <= time.time():
                    time.sleep(5)               # the listing still shows the market that just closed
                    continue
                # finish a market left over from an earlier run first
                try:
                    s = json.loads(STATE.read_text())
                    if s.get("ticker") and s["ticker"] != mk["ticker"]:
                        old = self.c.market(s["ticker"]) or {}
                        if old.get("close_time") and s["ticker"] not in done:
                            self.run_market(old, until, fh)
                            done.add(s["ticker"])
                            continue
                except Exception:
                    pass
                if not self.run_market(mk, until, fh):
                    break
                done.add(mk["ticker"])
                time.sleep(3)


def report():
    rows = [json.loads(l) for l in OUT.read_text().splitlines() if l.strip()] if OUT.exists() else []
    print(f"{len(rows)} markets settled")
    for m in ("strict", "loose"):
        r = [x[m] for x in rows if m in x]
        if not r:
            continue
        pn = sum(x["pnl"] for x in r)
        pf = sum(x["pnl_no_fee"] for x in r)
        fy = sum(x["yes_filled"] for x in r)
        fn = sum(x["no_filled"] for x in r)
        pairs = sum(x["pairs"] for x in r)
        print(f"  {m:6}: filled {fy:.0f} YES + {fn:.0f} NO ({pairs:.0f} matched pairs), "
              f"P&L ${pn:+.2f} after maker fees, ${pf:+.2f} with no fees, "
              f"markets with any fill {sum(1 for x in r if x['yes_filled'] + x['no_filled'] > 0)}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=115)
    ap.add_argument("--qty", type=int, default=10)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.report:
        return report()
    Maker(qty=a.qty).run(a.minutes)


if __name__ == "__main__":
    main()
