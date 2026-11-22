"""Same-venue box: the 15-minute window that closes on the hour vs Kalshi's hourly BTC markets.

KXBTC15M and the hourly KXBTCD ("above $X") settle on the same number: the
simple average of the sixty seconds of CF BRTI before the close. So in the last
15 minutes of every hour the two series price the same random variable S:

  15m YES (S >= K15) + hourly NO (S <= Kd), Kd >= K15   pays $1, or $2 if K15 <= S <= Kd
  15m NO  (S <  K15) + hourly YES (S >  Kd), Kd <  K15  pays $1, or $2 if Kd < S < K15

Both pay at least $1 whatever BTC does, with no basis risk (one feed, one
moment). If the two asks plus both taker fees cost under $1 the pair is
riskless; just above $1 it can still be worth it, since it pays $2 when S
lands between the strikes. Hourly strikes are $100 apart, so the nearest one is
at most ~$50 from the 15-minute strike.

During minutes :45-:59 of each hour this reads the 15-minute book and every
hourly strike's top of book (one request) about every 2 s, paper only.

  python -m kbot.hourly_watch --minutes 115
  python -m kbot.hourly_watch --report
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from .data import KALSHI, MaxyClient, _ts
from .maker_paper import levels

LOG = Path("reports/hourly_watch.jsonl")
BAND = 400.0                    # $: hourly strikes logged around the 15-minute strike


def fee(p):
    return 0.07 * p * (1 - p)


def hourly(c):
    d = c._get(f"{KALSHI}/events", {"series_ticker": "KXBTCD", "status": "open", "limit": 1,
                                    "with_nested_markets": "true"})
    ev = d["events"][0]
    return ev, ev["markets"]


def run(minutes, poll=2.0):
    c = MaxyClient("BTC", min_interval=0.25)
    until = time.time() + minutes * 60
    LOG.parent.mkdir(exist_ok=True)
    mk, close = None, 0
    with open(LOG, "a") as fh:
        while time.time() < until:
            now = time.time()
            if now % 3600 < 2700 - 5:                      # only the window that closes on the hour
                time.sleep(min(30.0, 2700 - 5 - now % 3600))
                continue
            try:
                if now >= close - 1 or mk is None:
                    mk = c.current_market()
                    close = _ts(mk["close_time"])
                if close % 3600 or close - now < 3:
                    time.sleep(poll)
                    continue
                t0 = time.time()
                yes, no = levels(c, mk["ticker"])
                ev, hs = hourly(c)
                t1 = time.time()
            except Exception:
                time.sleep(poll)
                continue
            if not yes or not no or _ts(hs[0]["close_time"]) != close:
                time.sleep(poll)
                continue
            k15 = float(mk["floor_strike"])
            kyb, knb = max(yes), max(no)
            row = {"ts": round(t0, 2), "read_s": round(t1 - t0, 2), "ticker": mk["ticker"], "event": ev["event_ticker"],
                   "left": int(close - t0), "k15": k15,
                   "yes_ask": round(1 - knb, 4), "yes_ask_n": no[knb], "no_ask": round(1 - kyb, 4), "no_ask_n": yes[kyb],
                   "h": [[float(m["floor_strike"]), float(m["yes_bid_dollars"]), float(m["yes_ask_dollars"]),
                          float(m.get("yes_bid_size_fp") or 0), float(m.get("yes_ask_size_fp") or 0)]
                         for m in hs if m.get("floor_strike") is not None and abs(m["floor_strike"] - k15) < BAND]}
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            time.sleep(max(0.0, poll - (time.time() - t0)))


def combos(r):
    """Every box in one read: (kind, hourly strike, cost incl. both taker fees, size)."""
    out = []
    for kd, yb, ya, ybn, yan in r["h"]:
        if kd >= r["k15"] and 0 < yb < 1:                  # 15m YES + hourly NO (NO ask = 1 - YES bid)
            a, b = r["yes_ask"], round(1 - yb, 4)
            out.append(("yes15_noH", kd, a + b + fee(a) + fee(b), min(r["yes_ask_n"], ybn)))
        if kd < r["k15"] and 0 < ya < 1:                   # 15m NO + hourly YES
            a, b = r["no_ask"], ya
            out.append(("no15_yesH", kd, a + b + fee(a) + fee(b), min(r["no_ask_n"], yan)))
    return out


def report():
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()] if LOG.exists() else []
    if not rows:
        print("no data yet")
        return
    c = MaxyClient("BTC")
    tks = sorted({r["ticker"] for r in rows})
    final = {}
    for tk in tks:
        m = c.market(tk) or {}
        if m.get("expiration_value"):
            final[tk] = float(m["expiration_value"])
    print(f"{len(rows)} reads over {len(tks)} hourly closes ({len(final)} settled), median read {np.median([r['read_s'] for r in rows]):.2f}s")
    best = []
    for r in rows:
        cs = combos(r)
        if cs:
            best.append(min(cs, key=lambda x: x[2]))
    cost = np.array([b[2] for b in best])
    print(f"cheapest box per read (cost incl. fees, pays >= $1): median ${np.median(cost):.3f}, "
          f"min ${cost.min():.3f}; under $1 in {np.mean(cost < 1):.1%} of reads")
    for thr in (1.0, 0.995, 0.99):
        hit = [(r, b) for r, b in zip(rows, best) if b[2] < thr]
        print(f"  cost < ${thr:.3f}: {len(hit)} reads in {len({r['ticker'] for r, _ in hit})} closes, "
              f"median size {np.median([b[3] for _, b in hit]) if hit else 0:.0f}")
    # paper: take every riskless box (cost < $1) once per 5 s, up to 50 per entry, settled on the final value
    pnl, n, last = 0.0, 0, {}
    for r in rows:
        if r["ticker"] not in final:
            continue
        S = final[r["ticker"]]
        for kind, kd, cst, sz in combos(r):
            if cst >= 1 or r["ts"] - last.get((r["ticker"], kind, kd), 0) < 5:
                continue
            q = min(sz, 50)
            pay = 1 + (r["k15"] <= S <= kd if kind == "yes15_noH" else kd < S < r["k15"])
            pnl += q * (pay - cst)
            n += q
            last[(r["ticker"], kind, kd)] = r["ts"]
    print(f"paper, every box under $1 (50 per entry, one per 5 s per strike): {n:.0f} boxes, P&L ${pnl:+.2f}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=115)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    report() if a.report else run(a.minutes)


if __name__ == "__main__":
    main()
