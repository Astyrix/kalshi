"""Live cross-venue check: Kalshi vs Polymarket on the same 15-minute BTC window.

Both venues list "BTC up or down" for identical 15-minute windows. If, at the
same moment, Polymarket's Up ask + Kalshi's NO ask (or Polymarket's Down ask +
Kalshi's YES ask) costs less than $1 after both taker fees, buying both locks
in the difference - unless the venues settle differently. They settle on
different feeds (Chainlink BTC/USD vs CF BRTI, each with its own strike) and
split in ~0.7% of windows, always when BTC closes near the strike
(kbot.basis_study).

Historical tapes suggested a small edge, but Polymarket's tape timestamps run
~3 s late, so only simultaneous live books settle it. About once a second this
reads both order books (paper only, nothing is sent) and logs the best pair
cost, the size available at it and the net edge after fees, plus both
settlement feeds: BRTI vs Kalshi's strike and Chainlink vs Polymarket's strike.

  python -m kbot.xvenue_watch --minutes 115
  python -m kbot.xvenue_watch --report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import ssl
import threading
import time
from pathlib import Path

import numpy as np
import requests

from .data import MaxyClient, _ts
from .maker_paper import levels
from .model import brti_vol, final_moves, move_scale  # noqa: F401  (re-exported for basis_study)

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


ZONE = 15.0      # $: the two venues have only ever split when BRTI closed within ~$10 of Kalshi's strike
def near_strike(dist, sig, left, band=ZONE):
    """P(BRTI settles within `band` of Kalshi's strike), the zone where the two venues can
    settle differently. dist = BRTI index - strike now, sig = brti_vol, left = seconds."""
    m = dist + final_moves(sig, left)
    return float(np.mean(np.abs(m) < band))


def brti(c, event):
    """(latest BRTI index, its 1 s volatility in $) from MaxyCharts."""
    v = [p for _, p in c.index_series(event, minutes=11)]
    return float(v[-1]), brti_vol(v)


RTDS = "wss://ws-live-data.polymarket.com"


class Chainlink:
    """Polymarket's live Chainlink BTC/USD stream (the feed its 15-minute markets settle on),
    one print per second (about 2 s behind spot), kept for the last 20 minutes in a background
    thread. Polymarket's strike (priceToBeat, published only after the window closes) and final
    price are not the print at the open/close but its 60-second average before it, like Kalshi's
    BRTI average: the mean of the 60 prints before the open matched priceToBeat to ~$0.6, the
    print at the open missed by $10-45."""

    def __init__(self):
        self.px, self.last = {}, None              # {second: price}, (second, price, received)
        threading.Thread(target=lambda: asyncio.run(self._loop()), daemon=True).start()

    async def _loop(self):
        import websockets
        sub = {"action": "subscribe", "subscriptions": [
            {"topic": "crypto_prices_chainlink", "type": "*", "filters": '{"symbol":"btc/usd"}'}]}
        ctx = ssl.create_default_context()
        while True:
            try:
                async with websockets.connect(RTDS, ssl=ctx, open_timeout=15, ping_interval=20) as ws:
                    await ws.send(json.dumps(sub))
                    async for m in ws:
                        try:                       # on subscribe: a snapshot of the last minute, then 1 print/s
                            d = json.loads(m)["payload"]
                            pts = d["data"] if "data" in d else [d]
                            pts = [(int(x["timestamp"]) // 1000, float(x["value"])) for x in pts]
                        except Exception:
                            continue
                        for t, v in pts:
                            self.px[t] = v
                        t, v = max(pts)
                        if not self.last or t >= self.last[0]:
                            self.last = (t, v, time.time())
                        for k in [k for k in self.px if k < t - 1200]:
                            del self.px[k]
            except Exception:
                await asyncio.sleep(3)

    def avg(self, end, n=60):
        """Mean of the prints in [end - n, end), or None if more than 10% are missing."""
        xs = [self.px[k] for k in range(int(end) - n, int(end)) if k in self.px]
        return sum(xs) / len(xs) if len(xs) >= 0.9 * n else None


def run(minutes, poll=1.0):
    c, s = MaxyClient("BTC", min_interval=0.25), requests.Session()
    s.headers["user-agent"] = "kbot-research/0.1"
    until = time.time() + minutes * 60
    LOG.parent.mkdir(exist_ok=True)
    tok, tk, close, strike, event, ptb = None, None, 0, None, None, None
    try:
        cl = Chainlink()
    except Exception:
        cl = None
    with open(LOG, "a") as fh:
        while time.time() < until:
            now = time.time()
            try:
                if now >= close - 1:
                    mk = c.current_market()
                    tk, close = mk["ticker"], _ts(mk["close_time"])
                    strike, event = mk.get("floor_strike"), mk.get("event_ticker")
                    tok = poly_tokens(s, close - 900)
                    ptb = None
                if ptb is None and cl is not None:
                    ptb = cl.avg(close - 900)
                if not tok or close - now < 20:
                    time.sleep(poll)
                    continue
                t0 = time.time()
                yes, no = levels(c, tk)
                up_a, up_n, up_t = best_ask(s, tok[0])
                dn_a, dn_n, _ = best_ask(s, tok[1])
                t1 = time.time()
                try:
                    bx, sig = brti(c, event)
                except Exception:
                    bx, sig = None, None
            except Exception:
                time.sleep(poll)
                continue
            if not yes or not no or up_a is None or dn_a is None:
                time.sleep(poll)
                continue
            kyb, knb = max(yes), max(no)                  # best YES bid, best NO bid on Kalshi
            k_no_ask, k_no_n = round(1 - kyb, 4), yes[kyb]  # buying NO lifts resting YES bids
            k_yes_ask, k_yes_n = round(1 - knb, 4), no[knb]
            row = {"ts": round(t0, 2), "read_s": round(t1 - t0, 2), "ticker": tk, "left": int(close - t0),
                   "k_yes_ask": k_yes_ask, "k_no_ask": k_no_ask, "p_up_ask": up_a, "p_dn_ask": dn_a,
                   "poly_age_s": round(t1 - up_t, 2) if up_t else None}
            if bx is not None and strike:
                row.update(brti=round(bx, 2), strike=float(strike), sig=round(sig, 3))
            if cl is not None and cl.last and t1 - cl.last[2] < 5:
                a60 = cl.avg(cl.last[0] + 1)
                row.update(cl=round(cl.last[1], 2), cl_age=round(t1 - cl.last[0], 2))
                if a60:
                    row["cl60"] = round(a60, 2)                # comparable to the BRTI index (also a 60 s average)
                if ptb:
                    row["ptb"] = round(ptb, 2)
            for name, pp, pn, kp, kn in (("upP_noK", up_a, up_n, k_no_ask, k_no_n),
                                         ("dnP_yesK", dn_a, dn_n, k_yes_ask, k_yes_n)):
                row[name] = {"cost": round(pp + kp, 3), "net": round(1 - pp - kp - pfee(pp) - kfee(kp), 4),
                             "size": round(min(pn, kn), 1)}
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            time.sleep(max(0.0, poll - (time.time() - t0)))


RESULTS = Path("state/xvenue-results.json")
SPLIT_RATE = 9 / 1326           # windows where the venues settled differently (kbot.basis_study)


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


FINALS = Path("state/xvenue-finals.json")


def finals(tickers):
    """{kalshi ticker: [kalshi strike, kalshi settlement value, polymarket strike, polymarket final]}
    for settled windows (Kalshi expiration_value, Gamma priceToBeat / finalPrice), cached."""
    try:
        out = json.loads(FINALS.read_text())
    except Exception:
        out = {}
    c, s = MaxyClient("BTC"), requests.Session()
    for tk, close in tickers.items():
        if tk in out or close > time.time() - 120:
            continue
        try:
            m = c.market(tk) or {}
            e = s.get(f"{GAMMA}/events", params={"slug": f"btc-updown-15m-{close - 900}"}, timeout=15).json()
            md = (e[0].get("eventMetadata") or {}) if e else {}
            if m.get("expiration_value") and md.get("finalPrice"):
                out[tk] = [float(m["floor_strike"]), float(m["expiration_value"]),
                           float(md["priceToBeat"]), float(md["finalPrice"])]
        except Exception:
            continue
    FINALS.parent.mkdir(exist_ok=True)
    FINALS.write_text(json.dumps(out))
    return out


def two_feed(rows, res):
    """Both settlement feeds live: BRTI vs Kalshi's strike and Chainlink vs Polymarket's strike.
    The venues split when the final BRTI move lands between the two distances, so the gap
    between them (and how it changes before the close) says which pair a split would hurt.

    Fits the change in the Chainlink-BRTI gap from each read to the close, by time left
    (gap_T - gap_t = a + b * gap_t + noise), then values each pair by simulation:
    E[pay] = P(Polymarket Up) + 1 - P(Kalshi YES) for Polymarket Up + Kalshi NO, with the
    final BRTI move drawn from the empirical table and the gap change from the fit."""
    live = [r for r in rows if "cl60" in r and "ptb" in r and "brti" in r and r["ticker"] in res]
    if not live:
        print("\ntwo-feed: no settled window with both feeds logged yet")
        return None
    closes = {r["ticker"]: int(round((r["ts"] + r["left"]) / 900)) * 900 for r in live}
    fin = finals(closes)
    live = [r for r in live if r["ticker"] in fin]
    wins = sorted({r["ticker"] for r in live})
    print(f"\ntwo-feed ({len(wins)} settled windows with Chainlink logged):")
    if not wins:
        return None
    err = [abs(next(r["ptb"] for r in live if r["ticker"] == tk) - fin[tk][2]) for tk in wins]
    print(f"  our Chainlink strike vs Polymarket's priceToBeat: median |diff| ${np.median(err):.2f}, max ${max(err):.2f}")
    print(f"  Kalshi strike from MaxyCharts vs Kalshi's: max |diff| "
          f"${max(abs(next(r['strike'] for r in live if r['ticker'] == tk) - fin[tk][0]) for tk in wins):.2f}")
    g_t = np.array([(r["cl60"] - r["ptb"]) - (r["brti"] - r["strike"]) for r in live])
    g_T = np.array([(fin[r["ticker"]][3] - fin[r["ticker"]][2]) - (fin[r["ticker"]][1] - fin[r["ticker"]][0])
                    for r in live])
    left = np.array([r["left"] for r in live])
    fit = {}
    print("  gap = (Chainlink - its strike) - (BRTI - Kalshi's strike); change from read to close:")
    for lo, hi in ((0, 60), (60, 180), (180, 420), (420, 900)):
        m = (left >= lo) & (left < hi)
        if m.sum() < 30:
            continue
        b, a = np.polyfit(g_t[m], g_T[m] - g_t[m], 1)
        sd = float(np.std(g_T[m] - g_t[m] - (a + b * g_t[m])))
        fit[(lo, hi)] = (a, b, sd)
        print(f"    {lo:3d}-{hi:3d}s left: n {m.sum():5d}, change = {a:+.2f} {b:+.2f} * gap, resid sd ${sd:.2f}"
              f" (gap sd ${np.std(g_t[m]):.2f})")
    return fit


GAP_BUCKETS = ((0, 60), (60, 180), (180, 420), (420, 10 ** 6))


def _both(r):
    return "cl60" in r and "ptb" in r and "brti" in r and "sig" in r


def gap_fit(rows, fin, tks):
    """Per time-left bucket, least squares of the settlement gap
    gap_T = (Polymarket final - its strike) - (Kalshi final - its strike) on the
    Chainlink-BRTI basis now and our strike difference (Polymarket's - Kalshi's).
    The strike difference is fixed once both opening averages are in, so it carries
    over nearly one for one; the basis only about half (it partly reverts by the close)."""
    tks = set(tks)
    X = {b: [] for b in GAP_BUCKETS}
    for r in rows:
        if r["ticker"] in tks and _both(r):
            f = fin[r["ticker"]]
            b = next(b for b in GAP_BUCKETS if b[0] <= r["left"] < b[1])
            X[b].append((r["cl60"] - r["brti"], r["ptb"] - r["strike"], (f[3] - f[2]) - (f[1] - f[0])))
    out = {}
    for b, v in X.items():
        if len(v) < 100:
            return None
        v = np.array(v)
        A = np.c_[np.ones(len(v)), v[:, 0], v[:, 1]]
        c = np.linalg.lstsq(A, v[:, 2], rcond=None)[0]
        out[b] = (c, float(np.std(v[:, 2] - A @ c)))
    return out


def pair_value(r, name, F, rng, n=4000):
    """Expected pay - cost of a pair with both feeds: BRTI's final distance to Kalshi's
    strike from the empirical move table, Chainlink's = that + the predicted gap + noise.
    Polymarket Up + Kalshi NO pays (Chainlink final >= its strike) + (BRTI final < Kalshi's).
    A split only goes one way for a given gap, so one pair of the two carries all the risk."""
    b = next(b for b in GAP_BUCKETS if b[0] <= r["left"] < b[1])
    c, sd = F[b]
    g = c[0] + c[1] * (r["cl60"] - r["brti"]) + c[2] * (r["ptb"] - r["strike"])
    m = final_moves(r["sig"], r["left"])
    if len(m) > n:
        m = m[rng.integers(0, len(m), n)]
    dk = r["brti"] - r["strike"] + m
    dp = dk + g + rng.normal(0, sd, len(m))
    if name == "upP_noK":
        pay = (dp >= 0).astype(float) + (dk < 0)
    else:
        pay = (dp < 0).astype(float) + (dk >= 0)
    return float(pay.mean()) - (1 - r[name]["net"])


def attach_brti(rows):
    """Give rows recorded before BRTI was logged live their BRTI / strike / volatility from the
    cached per-second index (the same numbers MaxyCharts shows live at that moment)."""
    need = {r["ticker"] for r in rows if "brti" not in r}
    if not need:
        return rows
    from .sweep import load_cached
    kw = {w.ticker: w for w in load_cached("BTC", 30) if w.ticker in need}
    for r in rows:
        w = kw.get(r["ticker"])
        if "brti" in r or w is None or not w.strike or not len(w.index):
            continue
        idx = np.asarray(w.index, dtype=float)
        j = np.searchsorted(idx[:, 0], r["ts"], side="right") - 1
        if j < 30:
            continue
        r["brti"], r["strike"] = float(idx[j, 1]), float(w.strike)
        r["sig"] = brti_vol(idx[:j + 1, 1])
    return rows


def paper(rows, res, thr=0.01, max_pairs=50, per_window=500, persist=False, gap=5.0, zone=None, late=0,
          value=None):
    """Buy min(size, max_pairs) pairs whenever the net edge after fees clears thr
    (optionally only if it was also there on the previous read), at most one entry
    per `gap` seconds and per_window pairs per window. Settled on both venues.
    zone: skip reads where P(BRTI ends within $15 of the strike) >= zone (split risk).
    late: fill at the prices and size of the read `late` reads after the signal (the
    order arrives after the book has moved), only if that read still has edge >= 0
    (a limit order at the signalled prices would not fill at a loss).
    value: f(row, pair) -> two-feed expected pay - cost; if given, the signal also needs
    value >= thr and a late fill value >= 0 (reads without both feeds are skipped)."""
    out, used, last, prev = [], {}, {}, {}
    nxt = {}
    if late:
        by_tk = {}
        for i, r in enumerate(rows):
            by_tk.setdefault(r["ticker"], []).append(i)
        for idx in by_tk.values():
            for a, b in zip(idx, idx[late:]):
                nxt[a] = b
    for i, r in enumerate(rows):
        if r["ticker"] not in res:
            continue
        if not (0 < r["k_yes_ask"] < 1 and 0 < r["k_no_ask"] < 1):
            continue                     # logged before sub-cent prices were kept (0.995 read as 1.00)
        if zone is not None:
            if "brti" not in r or near_strike(r["brti"] - r["strike"], r["sig"], r["left"]) >= zone:
                continue
        kres, pwin = res[r["ticker"]]
        for name in ("upP_noK", "dnP_yesK"):
            x = r[name]
            ok = x["net"] >= thr and (not persist or prev.get(name, -1) >= thr)
            prev[name] = x["net"]
            if not ok or r["ts"] - last.get(r["ticker"], 0) < gap:
                continue
            if value is not None and (not _both(r) or value(r, name) < thr):
                continue
            if late:
                j = nxt.get(i)
                if j is None or rows[j]["ts"] - r["ts"] > 3 * late + 2:
                    continue
                x = rows[j][name]
                if x["net"] < 0 or not (0 < rows[j]["k_yes_ask"] < 1 and 0 < rows[j]["k_no_ask"] < 1):
                    continue
                if value is not None and (not _both(rows[j]) or value(rows[j], name) < 0):
                    continue
            q = min(x["size"], max_pairs, per_window - used.get(r["ticker"], 0))
            if q < 1:
                continue
            if name == "upP_noK":
                pay = (pwin == "up") + (kres == "no")
            else:
                pay = (pwin == "down") + (kres == "yes")
            out.append((r["ticker"], q, q * (pay - (1 - x["net"])), pay))
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
    attach_brti(rows)
    splits = [tk for tk, (k, p) in res.items() if (k == "yes") != (p == "up")]
    print(f"\npaper trades (settled windows: {len(res)}, of which the venues split on {len(splits)}"
          f"{': ' + ', '.join(splits) if splits else ''}; 50 pairs max per entry, 500 per window):")
    two_feed(rows, res)
    norm = [tk for tk in res if tk not in splits]
    print(f"\n  per window: mean over the {len(norm)} normal windows | mean over split windows | expected at the "
          f"historical split rate {SPLIT_RATE:.2%}")
    for thr in (0.005, 0.01, 0.02):
        for persist, zone, late in ((False, None, 0), (True, None, 0), (False, 0.10, 0), (False, 0.15, 0),
                                    (False, None, 1), (False, 0.10, 1), (False, 0.10, 2)):
            t = paper(rows, res, thr=thr, persist=persist, zone=zone, late=late)
            if not t:
                continue
            pnl = sum(x[2] for x in t)
            pairs = sum(x[1] for x in t)
            tag = ((" & still there next read" if persist else "") + (f" & P(end within $15) < {zone:.0%}" if zone else "")
                   + (f", filled {late} read(s) later (limit)" if late else ""))
            gn = sum(x[2] for x in t if x[0] not in splits) / max(len(norm), 1)
            gs = sum(x[2] for x in t if x[0] in splits) / max(len(splits), 1)
            ev = (1 - SPLIT_RATE) * gn + SPLIT_RATE * gs if splits else gn
            print(f"  edge >= {thr * 100:.1f}c{tag:52}: {len(t):4d} entries, {pairs:6.0f} pairs, "
                  f"P&L ${pnl:+8.2f} | ${gn:+6.2f} | ${gs:+8.2f} | EV ${ev:+.2f}/window")

    two_feed_paper(rows, res, splits)


def two_feed_paper(rows, res, splits):
    """Out of sample: windows in order of close, the gap model refitted every 20 windows
    on all windows that closed before; the 1-read-late rule with and without the
    two-feed value check, on the same windows."""
    live = {}
    for r in rows:
        if _both(r) and r["ticker"] in res:
            live.setdefault(r["ticker"], int(round((r["ts"] + r["left"]) / 900)) * 900)
    fin = finals(live)
    tks = [tk for _, tk in sorted((c, tk) for tk, c in live.items() if tk in fin)]
    fits = {}
    for i in range(40, len(tks), 20):
        F = gap_fit(rows, fin, tks[:i])
        if F:
            fits.update({tk: F for tk in tks[i:i + 20]})
    if not fits:
        return
    rng = np.random.default_rng(0)
    test = {tk: res[tk] for tk in fits}
    sp = [tk for tk in fits if tk in splits]
    nn = max(len(fits) - len(sp), 1)
    print(f"\n  two-feed value check, out of sample ({len(fits)} windows after the first 40, gap model refit "
          f"every 20 on earlier windows; splits among them: {len(sp)}):")
    for thr in (0.005, 0.01):
        for tag, val in (("", None),
                         (" & two-feed value >= edge", lambda r, n: pair_value(r, n, fits[r["ticker"]], rng))):
            t = paper(rows, test, thr=thr, late=1, value=val)
            if not t:
                continue
            gn = sum(x[2] for x in t if x[0] not in splits) / nn
            gs = sum(x[2] for x in t if x[0] in splits) / max(len(sp), 1)
            ev = (1 - SPLIT_RATE) * gn + SPLIT_RATE * gs if sp else gn
            print(f"  edge >= {thr * 100:.1f}c, filled 1 read later (limit){tag:26}: {len(t):4d} entries, "
                  f"{sum(x[1] for x in t):6.0f} pairs, P&L ${sum(x[2] for x in t):+8.2f} | ${gn:+6.2f} | "
                  f"${gs:+8.2f} | EV ${ev:+.2f}/window")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=115)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    report() if a.report else run(a.minutes)


if __name__ == "__main__":
    main()
