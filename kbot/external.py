"""Data beyond MaxyCharts: Coinbase spot candles (volume, VWAP, basis), OKX
perpetual funding rates, and the economic calendar."""
from __future__ import annotations

import gzip
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests

NY = ZoneInfo("America/New_York")
CACHE = Path("data/external")
S = requests.Session()
S.headers["user-agent"] = "kbot-research/0.1"


def _get(url, params=None, tries=4):
    for k in range(tries):
        try:
            r = S.get(url, params=params, timeout=15)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if k == tries - 1:
                raise
            time.sleep(1.5 * 2 ** k)


# ---- Coinbase 1-minute candles --------------------------------------------------
def coinbase_minutes(product: str, start_ts: int, end_ts: int, pause=0.15) -> dict[int, tuple]:
    """{minute_ts: (open, high, low, close, volume_btc)} cached per UTC day."""
    CACHE.mkdir(parents=True, exist_ok=True)
    out = {}
    day = datetime.fromtimestamp(start_ts, timezone.utc).date()
    last = datetime.fromtimestamp(end_ts, timezone.utc).date()
    today = datetime.now(timezone.utc).date()
    while day <= last:
        p = CACHE / f"cb-{product}-{day}.json.gz"
        if p.exists():
            with gzip.open(p, "rt") as fh:
                rows = json.load(fh)
        else:
            rows = []
            d0 = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            for k in range(0, 1440, 300):
                a = d0 + timedelta(minutes=k)
                b = min(a + timedelta(minutes=299), now)
                if a >= now:
                    break                       # Coinbase rejects ranges in the future
                rows += _get(f"https://api.exchange.coinbase.com/products/{product}/candles",
                             {"granularity": 60, "start": a.isoformat(), "end": b.isoformat()})
                time.sleep(pause)
            if day < today:
                with gzip.open(p, "wt") as fh:
                    json.dump(rows, fh)
        for t, lo, hi, o, c, v in rows:
            out[int(t)] = (o, hi, lo, c, v)
        day += timedelta(days=1)
    return out


# ---- OKX funding ------------------------------------------------------------------
def okx_funding(start_ts: int, inst="BTC-USDT-SWAP") -> list[tuple[int, float]]:
    """[(funding_ts, rate)] oldest first, back to start_ts."""
    p = CACHE / f"okx-funding-{inst}.json"
    CACHE.mkdir(parents=True, exist_ok=True)
    rows, after = [], None
    while True:
        params = {"instId": inst, "limit": 100}
        if after:
            params["after"] = after
        d = _get("https://www.okx.com/api/v5/public/funding-rate-history", params)["data"]
        if not d:
            break
        rows += [(int(x["fundingTime"]) // 1000, float(x["fundingRate"])) for x in d]
        after = d[-1]["fundingTime"]
        if int(after) // 1000 < start_ts:
            break
        time.sleep(0.2)
    rows = sorted(set(rows))
    p.write_text(json.dumps(rows))
    return rows


# ---- calendar ------------------------------------------------------------------
def fomc_decisions(years=(2025, 2026)) -> list[int]:
    """FOMC statement times (2:00 pm ET on the meeting's last day)."""
    p = CACHE / "fomc.json"
    if p.exists():
        return json.loads(p.read_text())
    r = S.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", timeout=20)
    r.raise_for_status()
    t = r.text
    out = []
    for y in years:
        i = t.find(f"{y} FOMC Meetings")
        if i < 0:
            continue
        j = t.find("FOMC Meetings", i + 20)
        seg = t[i:j if j > 0 else i + 40000]
        for mon, days in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?'
                                    r'fomc-meeting__date[^>]*>([^<]+)<', seg, re.S):
            mon = mon.split("/")[-1]
            last_day = int(re.findall(r"\d+", days)[-1])
            try:
                d = datetime.strptime(f"{y} {mon} {last_day}", "%Y %B %d")
            except ValueError:
                continue
            out.append(int(datetime(d.year, d.month, d.day, 14, 0, tzinfo=NY).timestamp()))
    out.sort()
    CACHE.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out))
    return out


def calendar_feats(t: int, fomc: list[int]) -> dict[str, float]:
    ny = datetime.fromtimestamp(t, NY)
    mins = ny.hour * 60 + ny.minute
    wd = ny.weekday()
    weekday = wd < 5
    f = {
        "cal_hour_et": ny.hour + ny.minute / 60,
        "cal_weekday": float(wd),
        "cal_weekend": float(not weekday),
        # most US data (CPI, jobs, PPI, claims, retail sales) prints at 8:30 ET
        "cal_830_window": float(weekday and 8 * 60 + 25 <= mins <= 9 * 60 + 15),
        "cal_jobs_friday": float(wd == 4 and ny.day <= 7 and 8 * 60 + 25 <= mins <= 9 * 60 + 30),
        "cal_us_open": float(weekday and 9 * 60 + 25 <= mins <= 10 * 60 + 15),
        "cal_us_close": float(weekday and 15 * 60 + 45 <= mins <= 16 * 60 + 15),
        "cal_us_session": float(weekday and 9 * 60 + 30 <= mins < 16 * 60),
    }
    nxt = min((x - t for x in fomc if x >= t - 3 * 3600), default=10 ** 9)
    f["cal_fomc_day"] = float(any(datetime.fromtimestamp(x, NY).date() == ny.date() for x in fomc))
    f["cal_fomc_hours"] = max(min(nxt / 3600, 48), -3) if abs(nxt) < 48 * 3600 else 48.0
    return f
