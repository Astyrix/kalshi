"""Study real, named traders in Polymarket's BTC 15-minute up/down markets.

Kalshi's tape is anonymous; Polymarket's is not: every fill carries the
trader's wallet. Polymarket runs the same 15-minute BTC windows (resolved on
Chainlink's 60 s BTC/USD TWAP instead of CF's BRTI), so the people who
consistently win there show what actually works in this kind of market.

For every settled window it pulls all fills, then per wallet:
  P&L     buys pay price, sells receive it, held shares settle at $1 / $0
  style   entry price, seconds before close, trades per market, how often
          they hold BOTH outcomes (pair/arb style), how much they sell back
          before settlement (scalper) vs hold (directional)

  python -m kbot.poly_study --days 7
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com/v2"
CACHE = Path("data/cache/POLY-BTC15")


class Poly:
    def __init__(self, min_interval=0.15):
        self.s = requests.Session()
        self.s.headers["user-agent"] = "kbot-research/0.1"
        self.gap, self.last = min_interval, 0.0

    def get(self, url, params, retries=4):
        for k in range(retries):
            w = self.gap - (time.monotonic() - self.last)
            if w > 0:
                time.sleep(w)
            self.last = time.monotonic()
            try:
                r = self.s.get(url, params=params, timeout=20)
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(str(r.status_code))
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError):
                if k == retries - 1:
                    raise
                time.sleep(2 * 2 ** k)

    def window(self, start):
        """One settled window: {slug, start, end, winner, trades:[[ts, wallet, side, outcome_idx, price, size, tx]]}."""
        slug = f"btc-updown-15m-{start}"
        p = CACHE / f"{slug}.json.gz"
        if p.exists():
            with gzip.open(p, "rt") as fh:
                return json.load(fh)
        ev = self.get(f"{GAMMA}/events", {"slug": slug})
        if not ev:
            return None
        m = ev[0]["markets"][0]
        prices = json.loads(m.get("outcomePrices") or "[]")
        if m.get("umaResolutionStatus") != "resolved" or prices not in (["1", "0"], ["0", "1"]):
            return None
        winner = 0 if prices[0] == "1" else 1
        trades, cursor = [], None
        for _ in range(200):
            q = {"conditionId": m["conditionId"], "limit": 1000}
            if cursor:
                q["cursor"] = cursor
            d = self.get(f"{DATA}/trades", q)
            for x in d.get("data") or []:
                trades.append([x["timestamp"], x["proxy_wallet"], x["side"], x["outcome_index"],
                               x["price"], x["size"], x.get("transaction_hash", "")[:18]])
            pg = d.get("pagination") or {}
            cursor = pg.get("next_cursor")
            if not pg.get("has_more") or not cursor:
                break
        out = {"slug": slug, "start": start, "end": start + 900, "winner": winner,
               "volume": float(m.get("volume") or 0), "trades": trades}
        CACHE.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wt") as fh:
            json.dump(out, fh)
        return out


def wallet_stats(windows):
    W = defaultdict(lambda: {"pnl": 0.0, "bought": 0.0, "markets": {}, "n": 0, "sell_usd": 0.0,
                             "px": [], "left": [], "both": 0, "pair_cost": []})
    for w in windows:
        per = defaultdict(lambda: {"pnl": 0.0, "sh": [0.0, 0.0], "cost": [0.0, 0.0], "bsh": [0.0, 0.0]})
        for ts, wal, side, oi, px, sz, _ in w["trades"]:
            won = 1.0 if oi == w["winner"] else 0.0
            r = per[wal]
            if side == "BUY":
                r["pnl"] += sz * (won - px)
                r["sh"][oi] += sz
                r["cost"][oi] += sz * px
                r["bsh"][oi] += sz
                W[wal]["bought"] += sz * px
                W[wal]["px"].append((px, sz))
            else:
                r["pnl"] += sz * (px - won)
                r["sh"][oi] -= sz
                W[wal]["sell_usd"] += sz * px
            W[wal]["left"].append(w["end"] - ts)
            W[wal]["n"] += 1
        for wal, r in per.items():
            s = W[wal]
            s["pnl"] += r["pnl"]
            s["markets"][w["slug"]] = r["pnl"]
            if r["bsh"][0] > 0 and r["bsh"][1] > 0:
                s["both"] += 1
                # average cost of one Up + one Down share
                s["pair_cost"].append(r["cost"][0] / r["bsh"][0] + r["cost"][1] / r["bsh"][1])
    return W


def describe(s):
    v = np.array(list(s["markets"].values()))
    px = np.array(s["px"]) if s["px"] else np.zeros((0, 2))
    avg_px = float((px[:, 0] * px[:, 1]).sum() / px[:, 1].sum()) if len(px) else float("nan")
    z = v.mean() / (v.std(ddof=1) / math.sqrt(len(v))) if len(v) > 2 and v.std() > 0 else 0.0
    return {"pnl": round(s["pnl"], 2), "markets": len(v), "win_markets": round(float(np.mean(v > 0)), 3),
            "z": round(float(z), 2), "bought_usd": round(s["bought"], 2),
            "roi": round(s["pnl"] / s["bought"], 4) if s["bought"] else None,
            "trades_per_market": round(s["n"] / max(len(v), 1), 1), "avg_buy_px": round(avg_px, 3),
            "median_s_before_close": float(np.median(s["left"])) if s["left"] else None,
            "both_sides_share": round(s["both"] / max(len(v), 1), 3),
            "avg_pair_cost": round(float(np.mean(s["pair_cost"])), 4) if s["pair_cost"] else None,
            "sells_vs_buys": round(s["sell_usd"] / s["bought"], 3) if s["bought"] else None}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--min-markets", type=int, default=30)
    a = ap.parse_args(argv)
    P = Poly()
    now = int(time.time())
    last = now - now % 900 - 900                     # newest window that has surely ended
    starts = list(range(last - 900, int(last - a.days * 86400), -900))
    ws, t0 = [], time.time()
    for i, st in enumerate(starts):
        try:
            w = P.window(st)
        except Exception as e:
            print("skip", st, str(e)[:80], flush=True)
            continue
        if w:
            ws.append(w)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(starts)} windows [{time.time() - t0:.0f}s]", flush=True)
    n_tr = sum(len(w["trades"]) for w in ws)
    vol = sum(w["volume"] for w in ws)
    tx = defaultdict(int)
    for w in ws:
        for t in w["trades"]:
            tx[t[6]] += 1
    print(f"\n{len(ws)} settled windows, {n_tr} fills, ${vol:,.0f} volume, "
          f"{np.mean(list(tx.values())):.2f} fills per transaction")
    W = wallet_stats(ws)
    rows = {wal: describe(s) for wal, s in W.items()}
    tot = sum(r["pnl"] for r in rows.values())
    print(f"{len(rows)} wallets; sum of all wallet P&L ${tot:,.0f} "
          f"(near 0 means both sides of every fill are listed; else it is the takers' total)\n")
    reg = {w: r for w, r in rows.items() if r["markets"] >= a.min_markets}
    order = sorted(reg, key=lambda w: reg[w]["pnl"], reverse=True)
    cols = ["pnl", "markets", "win_markets", "z", "roi", "trades_per_market", "avg_buy_px",
            "median_s_before_close", "both_sides_share", "avg_pair_cost", "sells_vs_buys"]
    hdr = f"{'wallet':14} " + " ".join(f"{c[:10]:>10}" for c in cols)

    def show(title, ws_):
        print(title)
        print(hdr)
        for w in ws_:
            r = reg[w]
            print(f"{w[:12]:14} " + " ".join(f"{('-' if r[c] is None else r[c]):>10}" for c in cols))
        print()
    show(f"TOP 15 by P&L (wallets in >= {a.min_markets} markets)", order[:15])
    show("BOTTOM 10", order[-10:])

    # what separates winners from losers, among regulars
    def grp(f):
        g = [reg[w] for w in reg if f(reg[w])]
        if not g:
            return None
        keys = ["avg_buy_px", "median_s_before_close", "both_sides_share", "sells_vs_buys", "trades_per_market"]
        return {"wallets": len(g), "pnl": round(sum(r["pnl"] for r in g), 2),
                **{k: round(float(np.nanmedian([r[k] if r[k] is not None else np.nan for r in g])), 3) for k in keys}}
    groups = {
        "consistent winners (z >= 2)": grp(lambda r: r["z"] >= 2),
        "other winners": grp(lambda r: 0 < r["pnl"] and r["z"] < 2),
        "losers": grp(lambda r: r["pnl"] <= 0),
        "pair traders (both sides in >= 50% of markets)": grp(lambda r: r["both_sides_share"] >= 0.5),
        "directional (both sides in < 10%)": grp(lambda r: r["both_sides_share"] < 0.1),
        "scalpers (sell back >= 50% of buys)": grp(lambda r: (r["sells_vs_buys"] or 0) >= 0.5),
        "holders (sell back < 10%)": grp(lambda r: (r["sells_vs_buys"] or 0) < 0.1),
        "late (median < 120 s before close)": grp(lambda r: (r["median_s_before_close"] or 1e9) < 120),
        "early (median > 600 s before close)": grp(lambda r: (r["median_s_before_close"] or 0) > 600),
    }
    print("groups (medians):")
    for k, g in groups.items():
        print(f"  {k:48} {g}")
    Path("reports").mkdir(exist_ok=True)
    Path("reports/poly_study.json").write_text(json.dumps(
        {"windows": len(ws), "fills": n_tr, "volume": vol, "wallets": len(rows),
         "top": {w: reg[w] for w in order[:30]}, "bottom": {w: reg[w] for w in order[-15:]},
         "groups": groups}, indent=1))
    print("\nsaved reports/poly_study.json")


if __name__ == "__main__":
    main()
