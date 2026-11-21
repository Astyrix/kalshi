"""Live cross-venue check: Kalshi vs Polymarket on the same 15-minute BTC window.

Both venues list "BTC up or down" for identical 15-minute windows. If, at the
same moment, Polymarket's Up ask + Kalshi's NO ask (or Polymarket's Down ask +
Kalshi's YES ask) costs less than $1 after both taker fees, buying both locks
in the difference (they settle on different price feeds - Chainlink TWAP vs
CF BRTI - and disagree in ~2.5% of windows, which is the remaining risk).

Historical tapes suggested a small edge, but Polymarket's tape timestamps run
~3 s late, so only simultaneous live books settle it. Every ~2 s this reads
both order books (paper only, nothing is sent) and logs the best pair cost,
the size available at it, and the net edge after fees.

  python -m kbot.xvenue_watch --minutes 115
  python -m kbot.xvenue_watch --report
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import requests

from .data import MaxyClient, _ts
from .maker_paper import levels

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
LOG = Path("reports/xvenue_watch.jsonl")


def kfee(p):
    return 0.07 * p * (1 - p)


def pfee(p):                     # Polymarket crypto 15m: rate 0.05, exponent 1, takers only
    return 0.05 * p * (1 - p)


def poly_tokens(s, start):
    e = s.get(f"{GAMMA}/events", params={"slug": f"btc-updown-15m-{start}"}, timeout=15).json()
    if not e:
        return None
    m = e[0]["markets"][0]
    return json.loads(m["clobTokenIds"])          # [Up, Down]


def best_ask(s, token):
    b = s.get(f"{CLOB}/book", params={"token_id": token}, timeout=10).json()
    asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks") or []]
    if not asks:
        return None, 0.0, None
    p = min(a for a, _ in asks)
    return p, sum(q for a, q in asks if a == p), float(b.get("timestamp") or 0) / 1000


def run(minutes, poll=2.0):
    c, s = MaxyClient("BTC", min_interval=0.25), requests.Session()
    s.headers["user-agent"] = "kbot-research/0.1"
    until = time.time() + minutes * 60
    LOG.parent.mkdir(exist_ok=True)
    tok, tk, close = None, None, 0
    with open(LOG, "a") as fh:
        while time.time() < until:
            now = time.time()
            try:
                if now >= close - 1:
                    mk = c.current_market()
                    tk, close = mk["ticker"], _ts(mk["close_time"])
                    tok = poly_tokens(s, close - 900)
                if not tok or close - now < 20:
                    time.sleep(poll)
                    continue
                t0 = time.time()
                yes, no = levels(c, tk)
                up_a, up_n, up_t = best_ask(s, tok[0])
                dn_a, dn_n, _ = best_ask(s, tok[1])
                t1 = time.time()
            except Exception:
                time.sleep(poll)
                continue
            if not yes or not no or up_a is None or dn_a is None:
                time.sleep(poll)
                continue
            kyb, knb = max(yes), max(no)                  # best YES bid, best NO bid on Kalshi
            k_no_ask, k_no_n = round(1 - kyb, 2), yes[kyb]  # buying NO lifts resting YES bids
            k_yes_ask, k_yes_n = round(1 - knb, 2), no[knb]
            row = {"ts": round(t0, 2), "read_s": round(t1 - t0, 2), "ticker": tk, "left": int(close - t0),
                   "k_yes_ask": k_yes_ask, "k_no_ask": k_no_ask, "p_up_ask": up_a, "p_dn_ask": dn_a,
                   "poly_age_s": round(t1 - up_t, 2) if up_t else None}
            for name, pp, pn, kp, kn in (("upP_noK", up_a, up_n, k_no_ask, k_no_n),
                                         ("dnP_yesK", dn_a, dn_n, k_yes_ask, k_yes_n)):
                row[name] = {"cost": round(pp + kp, 3), "net": round(1 - pp - kp - pfee(pp) - kfee(kp), 4),
                             "size": round(min(pn, kn), 1)}
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            time.sleep(max(0.0, poll - (time.time() - t0)))


RESULTS = Path("state/xvenue-results.json")


def settle(rows):
    """{kalshi ticker: (kalshi result, polymarket winner 'up'/'down')} for windows that have settled."""
    try:
        res = json.loads(RESULTS.read_text())
    except Exception:
        res = {}
    c, s = MaxyClient("BTC"), requests.Session()
    for tk, close in {(r["ticker"], int(round((r["ts"] + r["left"]) / 900)) * 900) for r in rows}:
        if tk in res or close > time.time() - 120:
            continue
        try:
            m = c.market(tk) or {}
            e = s.get(f"{GAMMA}/events", params={"slug": f"btc-updown-15m-{close - 900}"},
                      timeout=15).json()
            pm_ = e[0]["markets"][0]
            pm = json.loads(pm_.get("outcomePrices") or "[]")
        except Exception:
            continue
        if m.get("result") in ("yes", "no") and pm_.get("umaResolutionStatus") == "resolved" \
                and pm in (["1", "0"], ["0", "1"]):
            res[tk] = [m["result"], "up" if pm[0] == "1" else "down"]
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(res))
    return res


def paper(rows, res, thr=0.01, max_pairs=50, per_window=500, persist=False, gap=5.0):
    """Buy min(size, max_pairs) pairs whenever the net edge after fees clears thr
    (optionally only if it was also there on the previous read), at most one entry
    per `gap` seconds and per_window pairs per window. Settled on both venues."""
    out, used, last, prev = [], {}, {}, {}
    for r in rows:
        if r["ticker"] not in res:
            continue
        kres, pwin = res[r["ticker"]]
        for name in ("upP_noK", "dnP_yesK"):
            x = r[name]
            ok = x["net"] >= thr and (not persist or prev.get(name, -1) >= thr)
            prev[name] = x["net"]
            if not ok or r["ts"] - last.get(r["ticker"], 0) < gap:
                continue
            q = min(x["size"], max_pairs, per_window - used.get(r["ticker"], 0))
            if q < 1:
                continue
            if name == "upP_noK":
                pay = (pwin == "up") + (kres == "no")
            else:
                pay = (pwin == "down") + (kres == "yes")
            fee_cost = (1 - x["net"]) - x["cost"]            # both fees per pair
            out.append((r["ticker"], q, q * (pay - x["cost"] - fee_cost), pay))
            used[r["ticker"]] = used.get(r["ticker"], 0) + q
            last[r["ticker"]] = r["ts"]
    return out


def report():
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()] if LOG.exists() else []
    if not rows:
        print("no data yet")
        return
    best = np.array([max(r["upP_noK"]["net"], r["dnP_yesK"]["net"]) for r in rows])
    gap = np.array([(r["p_up_ask"] + r["p_dn_ask"] - 1) - 0 for r in rows])
    diff = np.array([((r["p_up_ask"] - (1 - r["p_dn_ask"])) / 2 + (1 - r["p_dn_ask"])) -
                     ((r["k_yes_ask"] + 1 - r["k_no_ask"]) / 2) for r in rows])
    print(f"{len(rows)} simultaneous book reads over {len({r['ticker'] for r in rows})} windows "
          f"(median read time {np.median([r['read_s'] for r in rows]):.2f}s)")
    print(f"Polymarket mid - Kalshi mid for Up: median {np.median(diff) * 100:+.2f}c, "
          f"mean abs {np.mean(np.abs(diff)) * 100:.2f}c; Polymarket Up+Down asks - $1: median {np.median(gap) * 100:+.1f}c")
    print(f"best pair net edge after both fees: median {np.median(best) * 100:+.2f}c")
    for c_ in (0.0, 0.005, 0.01, 0.02):
        m = best > c_
        sizes = [max((r[k]["size"] for k in ("upP_noK", "dnP_yesK") if r[k]["net"] > c_), default=0)
                 for r, ok in zip(rows, m) if ok]
        print(f"  net > {c_ * 100:.1f}c: {m.mean():6.2%} of reads, median size {np.median(sizes) if sizes else 0:.0f} "
              f"contracts, windows with any {len({r['ticker'] for r, ok in zip(rows, m) if ok})}")
    ages = [r["poly_age_s"] for r in rows if r.get("poly_age_s") is not None]
    if ages:
        print(f"Polymarket book age when read: median {np.median(ages):.2f}s, 90th pct {np.quantile(ages, .9):.2f}s")
    res = settle(rows)
    print(f"\npaper trades (settled windows: {len(res)}; both venues' real results; 50 pairs max per entry):")
    for thr in (0.005, 0.01, 0.02):
        for persist in (False, True):
            t = paper(rows, res, thr=thr, persist=persist)
            if not t:
                continue
            pnl = sum(x[2] for x in t)
            pairs = sum(x[1] for x in t)
            split = sum(1 for x in t if x[3] != 1)
            print(f"  edge >= {thr * 100:.1f}c{' & still there 2 s later' if persist else '':26}: {len(t):4d} entries, "
                  f"{pairs:7.0f} pairs, P&L ${pnl:+8.2f} ({pnl / pairs * 100:+.2f}c/pair), "
                  f"{len({x[0] for x in t})} windows, {split} entries where the venues settled differently")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=115)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    report() if a.report else run(a.minutes)


if __name__ == "__main__":
    main()
