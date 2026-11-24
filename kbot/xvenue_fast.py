"""How long do cross-venue gaps last? A fast probe of the Polymarket + Kalshi pairs.

The 1-second recorder (kbot.xvenue_watch) shows the pairs at signal earn about 3x what
an order arriving one read (about 1 s) later earns, so the money is in how fast the
gaps close. This reads the Kalshi book straight from Kalshi's public API (about 0.25 s
fresher than the MaxyCharts relay) and both Polymarket books, all three in parallel,
4 times a second, and logs both pairs' net edge after fees. Paper only: no orders.

The report treats a read with net edge >= thr as a signal and fills a limit order at
the signal's prices d seconds later only if that later read still has net edge >= 0,
for d = 0 (instant), 0.25, 0.5, 1 and 2 s, settled with the recorder's results.

  python -m kbot.xvenue_fast --minutes 115
  python -m kbot.xvenue_fast --report
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

from .data import MaxyClient, _ts
from .xvenue_watch import RESULTS, best_ask, kfee, pfee, poly_tokens

DIRECT = "https://api.elections.kalshi.com/trade-api/v2"
LOG = Path("reports/xvenue_fast.jsonl")


def kbook(s, tk):
    r = s.get(f"{DIRECT}/markets/{tk}/orderbook", params={"depth": 5}, timeout=5)
    r.raise_for_status()
    d = r.json()["orderbook_fp"]
    yes = {round(float(p), 4): float(q) for p, q in d.get("yes_dollars") or []}
    no = {round(float(p), 4): float(q) for p, q in d.get("no_dollars") or []}
    return yes, no


def run(minutes, poll=0.25):
    c = MaxyClient("BTC")
    ks, ps = requests.Session(), requests.Session()
    for s in (ks, ps):
        s.headers["user-agent"] = "kbot-research/0.1"
    ex = ThreadPoolExecutor(3)
    until = time.time() + minutes * 60
    LOG.parent.mkdir(exist_ok=True)
    tok, tk, close, errs = None, None, 0, 0
    with open(LOG, "a") as fh:
        while time.time() < until:
            now = time.time()
            try:
                if now >= close - 1:
                    mk = c.current_market()
                    tk, close = mk["ticker"], _ts(mk["close_time"])
                    tok = poly_tokens(ps, close - 900)
                if not tok or close - now < 20:
                    time.sleep(1)
                    continue
                t0 = time.time()
                fk = ex.submit(kbook, ks, tk)
                fu, fd = ex.submit(best_ask, ps, tok[0]), ex.submit(best_ask, ps, tok[1])
                (yes, no), (up_a, up_n, _), (dn_a, dn_n, _) = fk.result(), fu.result(), fd.result()
                t1 = time.time()
            except Exception:
                errs += 1
                time.sleep(1.0 if errs % 10 == 0 else poll)
                continue
            if not yes or not no or up_a is None or dn_a is None:
                time.sleep(poll)
                continue
            kyb, knb = max(yes), max(no)
            k_no_ask, k_no_n = round(1 - kyb, 4), yes[kyb]
            k_yes_ask, k_yes_n = round(1 - knb, 4), no[knb]
            row = {"ts": round(t0, 3), "read_s": round(t1 - t0, 3), "ticker": tk, "left": round(close - t0, 2)}
            for name, pp, pn, kp, kn in (("upP_noK", up_a, up_n, k_no_ask, k_no_n),
                                         ("dnP_yesK", dn_a, dn_n, k_yes_ask, k_yes_n)):
                row[name] = [round(1 - pp - kp - pfee(pp) - kfee(kp), 4), round(min(pn, kn), 1)]
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            time.sleep(max(0.0, poll - (time.time() - t0)))


def report(thr=0.005, max_pairs=50, per_window=500, gap=5.0):
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()] if LOG.exists() else []
    if not rows:
        print("no data yet")
        return
    try:
        res = json.loads(RESULTS.read_text())
    except Exception:
        res = {}
    by = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r)
    rt = np.array([r["read_s"] for r in rows])
    print(f"{len(rows)} reads over {len(by)} windows, read time median {np.median(rt) * 1000:.0f} ms "
          f"(p90 {np.quantile(rt, .9) * 1000:.0f}), median spacing "
          f"{np.median(np.diff([r['ts'] for r in rows])):.2f} s")
    # how long a gap lasts: consecutive reads with net >= thr, per pair
    runs = []
    for rs in by.values():
        for name in ("upP_noK", "dnP_yesK"):
            start = None
            for a, b in zip(rs, rs[1:] + [None]):
                if a[name][0] >= thr and start is None:
                    start = a["ts"]
                if start is not None and (b is None or b[name][0] < thr or b["ts"] - a["ts"] > 1.5):
                    runs.append(a["ts"] - start + 0.25)
                    start = None
    if runs:
        runs = np.array(runs)
        print(f"gaps with net edge >= {thr * 100:.1f}c: {len(runs)}, lasting median {np.median(runs):.2f} s; "
              f"{np.mean(runs <= 0.25):.0%} gone by the next read, {np.mean(runs > 1):.0%} last over 1 s, "
              f"{np.mean(runs > 2):.0%} over 2 s")
    settled = [tk for tk in by if tk in res]
    splits = [tk for tk in settled if (res[tk][0] == "yes") != (res[tk][1] == "up")]
    print(f"paper fills ({len(settled)} settled windows, {len(splits)} split; {max_pairs} pairs max per entry, "
          f"{per_window} per window, one entry per {gap:.0f} s):")
    for d in (0, 0.25, 0.5, 1.0, 2.0):
        pnl, n, pairs = 0.0, 0, 0
        for tk in settled:
            rs, kres, pwin = by[tk], *res[tk]
            ts = np.array([r["ts"] for r in rs])
            used, last = 0, -1e9
            for i, r in enumerate(rs):
                for name in ("upP_noK", "dnP_yesK"):
                    if r[name][0] < thr or r["ts"] - last < gap:
                        continue
                    j = i if d == 0 else int(np.searchsorted(ts, r["ts"] + d - 0.05))
                    if j >= len(rs) or rs[j]["ts"] - r["ts"] > d + 0.6:
                        continue
                    net, size = rs[j][name]
                    if net < 0:
                        continue
                    q = min(size, max_pairs, per_window - used)
                    if q < 1:
                        continue
                    pay = (pwin == "up") + (kres == "no") if name == "upP_noK" else (pwin == "down") + (kres == "yes")
                    pnl += q * (pay - (1 - net))
                    n, pairs, used, last = n + 1, pairs + q, used + q, r["ts"]
        nn = max(len(settled) - len(splits), 1)
        print(f"  filled {d:4.2f} s after the signal (limit): {n:4d} entries, {pairs:6.0f} pairs, P&L ${pnl:+8.2f}, "
              f"${pnl / max(len(settled), 1):+.2f}/window")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=115)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    report() if a.report else run(a.minutes)


if __name__ == "__main__":
    main()
