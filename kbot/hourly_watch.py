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

During minutes :45-:59 of each hour this reads the 15-minute book and the live
order books of the hourly strikes on either side of its strike about every
1.5 s, paper only. (The events endpoint's per-market bid/ask is a snapshot that
runs 15-20 s stale, so only /orderbook is used for prices.)

History: for every hour close, the trade tapes of the 15-minute window and of
the two hourly strikes around its strike, last 15 minutes. A box "was there"
in a second when takers bought both of its legs in that second; filled d
seconds later means takers again traded both legs at or below those prices.

  python -m kbot.hourly_watch --minutes 115
  python -m kbot.hourly_watch --report
  python -m kbot.hourly_watch --fetch-hist 7     # download tapes (about 20 s per hour close)
  python -m kbot.hourly_watch --hist
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from .data import KALSHI, MaxyClient, _ts
from .maker_paper import levels

LOG = Path("reports/hourly_watch.jsonl")
NEAR = 2                        # hourly strikes read on each side of the 15-minute strike


def fee(p):
    return 0.07 * p * (1 - p)


def hourly(c):
    d = c._get(f"{KALSHI}/events", {"series_ticker": "KXBTCD", "status": "open", "limit": 1,
                                    "with_nested_markets": "true"})
    ev = d["events"][0]
    return ev, ev["markets"]


def top(book_side):
    p = max(book_side)
    return p, book_side[p]


def run(minutes, poll=1.5):
    c = MaxyClient("BTC", min_interval=0.2)
    until = time.time() + minutes * 60
    LOG.parent.mkdir(exist_ok=True)
    mk, close, strikes = None, 0, []
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
                    strikes = []
                if close % 3600 or close - now < 3:
                    time.sleep(poll)
                    continue
                k15 = float(mk["floor_strike"])
                if not strikes:                            # this hour's strikes (tickers only; prices come from books)
                    ev, hs = hourly(c)
                    if _ts(hs[0]["close_time"]) != close:
                        time.sleep(poll)
                        continue
                    hs = sorted((float(m["floor_strike"]), m["ticker"]) for m in hs if m.get("floor_strike") is not None)
                    strikes = [x for x in hs if x[0] < k15][-NEAR:] + [x for x in hs if x[0] >= k15][:NEAR]
                t0 = time.time()
                yes, no = levels(c, mk["ticker"])
                h = []
                for kd, tk in strikes:
                    hy, hn = levels(c, tk)
                    if hy and hn:
                        (yb, ybn), (nb, nbn) = top(hy), top(hn)
                        h.append([kd, yb, round(1 - nb, 4), ybn, nbn])   # strike, YES bid, YES ask, sizes
                t1 = time.time()
            except Exception:
                time.sleep(poll)
                continue
            if not yes or not no or not h:
                time.sleep(poll)
                continue
            (kyb, kybn), (knb, knbn) = top(yes), top(no)
            row = {"ts": round(t0, 2), "read_s": round(t1 - t0, 2), "ticker": mk["ticker"], "left": int(close - t0),
                   "k15": k15, "yes_ask": round(1 - knb, 4), "yes_ask_n": knbn, "no_ask": round(1 - kyb, 4),
                   "no_ask_n": kybn, "h": h}
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


HIST = Path("data/cache/BOX")


def _trades(c, ticker, lo, hi):
    rows, cur = [], None
    for url in (f"{KALSHI}/markets/trades", f"{KALSHI}/historical/trades"):
        for _ in range(60):
            q = {"ticker": ticker, "limit": 1000, "min_ts": lo, "max_ts": hi}
            if cur:
                q["cursor"] = cur
            d = c._get(url, q)
            tr = d.get("trades") or []
            rows += [(datetime.fromisoformat(x["created_time"].replace("Z", "+00:00")).timestamp(),
                      float(x["yes_price_dollars"]), float(x.get("count_fp") or 0), x.get("taker_side") == "yes")
                     for x in tr]
            cur = d.get("cursor")
            if not cur or not tr:
                break
        if rows:
            break
        cur = None
    return sorted(rows)


def fetch_hist(days):
    c = MaxyClient("BTC", min_interval=0.3)
    now = time.time()
    ms = [m for m in c.settled_markets(int(now - days * 86400), int(now)) if _ts(m["close_time"]) % 3600 == 0]
    HIST.mkdir(parents=True, exist_ok=True)
    for m in ms:
        close, p = _ts(m["close_time"]), HIST / f"{m['ticker']}.json"
        if p.exists():
            continue
        ev = f"KXBTCD-{datetime.fromtimestamp(close, ZoneInfo('America/New_York')):%y%b%d%H}".upper()
        try:
            e = c._get(f"{KALSHI}/events/{ev}", {"with_nested_markets": "true"})
            hs = e.get("markets") or e.get("event", {}).get("markets") or []
            k15 = float(m["floor_strike"])
            ks = sorted(float(h["floor_strike"]) for h in hs if h.get("floor_strike") is not None)
            tk = {float(h["floor_strike"]): h["ticker"] for h in hs if h.get("floor_strike") is not None}
            rec = {"close": close, "k15": k15, "S": float(m["expiration_value"]), "event": ev,
                   "up": min((k for k in ks if k >= k15), default=None),
                   "dn": max((k for k in ks if k < k15), default=None),
                   "t15": _trades(c, m["ticker"], close - 900, close)}
            for name in ("up", "dn"):
                rec["t_" + name] = _trades(c, tk[rec[name]], close - 900, close) if rec[name] is not None else []
            p.write_text(json.dumps(rec))
            print(m["ticker"], ev, len(rec["t15"]), len(rec["t_up"]), len(rec["t_dn"]), flush=True)
        except Exception as ex:
            print("fail", m["ticker"], ev, str(ex)[:80], flush=True)


def _taken(tr, side_yes, lo, hi):
    """{second: [cheapest price takers paid for that side, contracts traded at it]} (NO = 1 - YES price)."""
    out = {}
    for t, y, n, ty in tr:
        if ty != side_yes or not lo <= t < hi:
            continue
        s, px = int(t), (y if side_yes else round(1 - y, 4))
        if not 0 < px < 1:
            continue
        if s not in out or px < out[s][0] - 1e-9:
            out[s] = [px, n]
        elif abs(px - out[s][0]) < 1e-9:
            out[s][1] += n
    return out


def hist():
    files = sorted(HIST.glob("*.json"))
    if not files:
        print("no history: run --fetch-hist first")
        return
    res = {}
    for p in files:
        r = json.loads(p.read_text())
        close, k15, S = r["close"], r["k15"], r["S"]
        lo, hi = close - 900, close - 3
        for k, leg15, legH, kind in ((r["up"], _taken(r["t15"], True, lo, hi), _taken(r["t_up"], False, lo, hi), "yes"),
                                      (r["dn"], _taken(r["t15"], False, lo, hi), _taken(r["t_dn"], True, lo, hi), "no")):
            if k is None:
                continue
            pay = 1 + ((k15 <= S <= k) if kind == "yes" else (k < S < k15))
            for d in (0, 1, 2, 5):
                last = -99
                for s in sorted(set(leg15) & set(legH)):
                    a, b = leg15[s][0], legH[s][0]
                    if a + b + fee(a) + fee(b) >= 1 or s - last < 5:
                        continue
                    x, y = (leg15[s], legH[s]) if d == 0 else (leg15.get(s + d), legH.get(s + d))
                    if not x or not y or x[0] > a + 1e-9 or y[0] > b + 1e-9:
                        continue
                    q = min(x[1], y[1], 50)
                    if q < 1:
                        continue
                    res.setdefault(d, []).append((p.stem, q, q * (pay - x[0] - y[0] - fee(x[0]) - fee(y[0]))))
                    last = s
    n = len(files)
    print(f"{n} hour closes; boxes that cost < $1 after fees on the tape, one entry per 5 s, up to 50 boxes:")
    for d, v in sorted(res.items()):
        pnl, q = sum(x[2] for x in v), sum(x[1] for x in v)
        print(f"  {'at the signal' if not d else f'filled {d} s later (limit)':28}: {len(v):4d} entries in "
              f"{len({x[0] for x in v}):3d} closes, {q:6.0f} boxes, P&L ${pnl:+8.2f} ({pnl / q * 100:+.2f}c/box), "
              f"${pnl / n:+.2f} per hour")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=115)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--fetch-hist", type=float, default=0, metavar="DAYS")
    ap.add_argument("--hist", action="store_true")
    a = ap.parse_args(argv)
    if a.fetch_hist:
        fetch_hist(a.fetch_hist)
    elif a.hist:
        hist()
    elif a.report:
        report()
    else:
        run(a.minutes)


if __name__ == "__main__":
    main()
