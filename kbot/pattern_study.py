"""Which candle and chart patterns actually predict the next move?

For every completed candle on 1m / 5m / 15m, detect every pattern, then
measure the move over the next horizon in units of normal volatility. Done
on two sources:

  index     the MaxyCharts BRTI series (what Kalshi settles on, but smoothed)
  coinbase  real Coinbase BTC-USD 1-minute OHLC (unsmoothed)

A pattern counts as reliable only if it moves the right way in BOTH halves
of the data with |t| >= 3 overall (there are ~250 tests, so |t| ~ 2 happens
by chance). Results go to reports/patterns.json, which the live trader uses
as its prior and keeps updating with what it sees (see pattern_memory.py).

  python -m kbot.pattern_study --days 70
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from . import external as E
from .patterns import CHART_PATTERNS, PATTERNS, Series, chart_patterns, detect

TFS = {"1m": 60, "5m": 300, "15m": 900}
HORIZON = {"1m": 5, "5m": 3, "15m": 1}       # in candles of that timeframe -> 5m, 15m, 15m


def candles_from_seconds(series: Series, tf: int):
    """(start_ts, O, H, L, C) arrays of clock-aligned candles; NaN when sparse."""
    a = series.t0 + (-series.t0) % tf
    n = (series.t0 + len(series.v) - a) // tf
    seg = series.v[a - series.t0: a - series.t0 + n * tf].reshape(n, tf)
    ok = np.sum(~np.isnan(seg), axis=1) >= tf * 0.5
    with np.errstate(all="ignore"):
        o = np.array([r[~np.isnan(r)][0] if k else np.nan for r, k in zip(seg, ok)])
        c = np.array([r[~np.isnan(r)][-1] if k else np.nan for r, k in zip(seg, ok)])
        h, l = np.nanmax(seg, axis=1), np.nanmin(seg, axis=1)
    h[~ok] = l[~ok] = np.nan
    return a + tf * np.arange(n), o, h, l, c


def candles_from_minutes(cb: dict, tf: int):
    ts = np.array(sorted(cb))
    a = ts[0] + (-ts[0]) % tf
    n = (ts[-1] - a) // tf
    o, h, l, c = (np.full(n, np.nan) for _ in range(4))
    per = tf // 60
    for k in range(n):
        rows = [cb.get(int(a + k * tf + 60 * j)) for j in range(per)]
        rows = [r for r in rows if r]
        if len(rows) >= max(per // 2, 1):
            o[k], h[k], l[k], c[k] = rows[0][0], max(r[1] for r in rows), min(r[2] for r in rows), rows[-1][3]
    return a + tf * np.arange(n), o, h, l, c


def study(candles, tf_name, half_split_ts):
    ts, o, h, l, c = candles
    hz = HORIZON[tf_name]
    r = np.diff(np.log(c))
    out = {}
    for k in range(40, len(ts) - hz):
        cs = [None if math.isnan(c[j]) else (o[j], h[j], l[j], c[j]) for j in range(k - 30, k + 1)]
        if cs[-1] is None or math.isnan(c[k + hz]):
            continue
        rr = r[k - 30:k]
        rr = rr[~np.isnan(rr)]
        if len(rr) < 15:
            continue
        sig = float(np.sqrt(np.mean(rr * rr)))           # per-candle log vol
        if sig <= 0:
            continue
        unit = sig * c[k]
        move = (c[k + hz] - c[k]) / (unit * math.sqrt(hz))
        late = int(ts[k] >= half_split_ts)
        found = {**{f"pat_{p}": v for p, v in detect(cs).items()},
                 **{f"chart_{p}": v for p, v in chart_patterns(cs, unit).items()}}
        found["_all"] = 1.0
        for p, v in found.items():
            if v:
                s = out.setdefault(p, [[], []])
                s[late].append(move)
    return out


def summarize(raw, direction):
    res = {}
    base = raw.pop("_all")
    allm = np.array(base[0] + base[1])
    mu0, absm0 = float(allm.mean()), float(np.abs(allm).mean())
    for p, (a, b) in raw.items():
        m = np.array(a + b)
        if len(m) < 30:
            continue
        d = direction.get(p.split("_", 1)[1], 0)
        exc = m - mu0
        t = float(exc.mean() / (exc.std(ddof=1) / math.sqrt(len(m)))) if len(m) > 2 else 0.0
        e = {"n": len(m), "mean_move": round(float(exc.mean()), 4), "t": round(t, 2),
             "up_rate": round(float(np.mean(m > 0)), 4),
             "expansion": round(float(np.abs(m).mean() / absm0), 3),
             "first_half": round(float(np.mean(a) - mu0), 4) if a else None,
             "second_half": round(float(np.mean(b) - mu0), 4) if b else None,
             "textbook": d}
        halves = [x for x in (e["first_half"], e["second_half"]) if x is not None]
        same = len(halves) == 2 and np.sign(halves[0]) == np.sign(halves[1]) != 0
        e["reliable"] = bool(abs(t) >= 3 and same)
        e["acts"] = "continuation-up" if e["mean_move"] > 0 else "continuation-down"
        if d:
            e["textbook_right"] = bool(np.sign(e["mean_move"]) == d) if e["reliable"] else None
        res[p] = e
    return res


def main(argv=None):
    from .sweep import load_cached
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=70)
    a = ap.parse_args(argv)
    ws = load_cached("BTC", a.days)
    series = Series(ws)
    a0, a1 = series.t0, series.t0 + len(series.v)
    split = (a0 + a1) // 2
    cb = E.coinbase_minutes("BTC-USD", a0, a1)
    direction = {**PATTERNS, **CHART_PATTERNS}
    report = {}
    for src in ("index", "coinbase"):
        for name, tf in TFS.items():
            cnd = candles_from_seconds(series, tf) if src == "index" else candles_from_minutes(cb, tf)
            res = summarize(study(cnd, name, split), direction)
            report[f"{src}:{name}"] = res
            rel = sorted(((p, e) for p, e in res.items() if e["reliable"]), key=lambda x: -abs(x[1]["t"]))
            print(f"\n== {src} {name} candles, move over next {HORIZON[name] * tf // 60} min "
                  f"({len(res)} patterns seen, {len(rel)} reliable) ==")
            print(f"  {'pattern':26} {'n':>6} {'move(σ)':>8} {'t':>6} {'up%':>6} {'1st':>7} {'2nd':>7} "
                  f"{'size':>5}  textbook")
            for p, e in rel:
                tb = {1: "bull", -1: "bear", 0: "-"}[e["textbook"]]
                ok = "" if e["textbook"] == 0 else ("RIGHT" if e.get("textbook_right") else "WRONG")
                print(f"  {p:26} {e['n']:6d} {e['mean_move']:+8.3f} {e['t']:+6.1f} {e['up_rate']:6.1%} "
                      f"{e['first_half']:+7.3f} {e['second_half']:+7.3f} {e['expansion']:5.2f}  {tb} {ok}")
            big = sorted(((p, e) for p, e in res.items() if e["expansion"] >= 1.15 and e["n"] >= 100),
                         key=lambda x: -x[1]["expansion"])[:5]
            if big:
                print("  bigger-than-normal moves after (direction unknown): " +
                      ", ".join(f"{p} x{e['expansion']}" for p, e in big))
    Path("reports").mkdir(exist_ok=True)
    Path("reports/patterns.json").write_text(json.dumps(report, indent=1))
    print("\nsaved reports/patterns.json")


if __name__ == "__main__":
    main()
