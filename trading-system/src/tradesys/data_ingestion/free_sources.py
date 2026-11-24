"""Free, no-login data sources (used because no Databento account is available).

  dukascopy   S&P 500 index CFD (USA500.IDX/USD) 1-minute bid candles, from 2011-09.
              Index points, not SPY; tick volume, not share volume. Throttled hard by the
              vendor: fetched politely (one file every ~2 s, backoff on 429/503) and cached raw.
  histdata    S&P 500 CFD (SPXUSD) 1-minute bars 2010-11..2018-12 from a public GitHub
              mirror (FutureSharks/financial-data). Timestamps are EST all year (no DST).
              No volume. Used as an independent cross-check of the Dukascopy bars.
  yahoo       SPY / ^GSPC / ^VIX daily bars since 1993 (unadjusted OHLC + adjusted close),
              and real SPY 1-minute bars for roughly the last 30 days (8-day requests).
              Unofficial endpoint: may change or rate-limit.

SPY-equivalent proxy bars: index bars scaled each session by (SPY close / S&P 500 close) of
the PREVIOUS session (known before the open), so prices, stops and the $0.01 tick live in
SPY dollars. Intraday returns are the index's. Volume is not SPY's.
"""
from __future__ import annotations

import datetime as dt
import logging
import lzma
import struct
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from ..market_calendar import NY, sessions

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (research; tradesys)"}
DUKA = "https://datafeed.dukascopy.com/datafeed"
DUKA_FIRST = dt.date(2011, 9, 19)


# ------------------------------------------------------------------ Dukascopy
def duka_path(root: Path, instrument: str, day: dt.date, side: str = "BID") -> Path:
    return Path(root) / "raw" / "dukascopy" / instrument / side / f"{day:%Y}" / f"{day:%Y-%m-%d}.bi5"


def duka_fetch_day(s: requests.Session, root: Path, instrument: str, day: dt.date, side: str = "BID",
                   pause: float = 2.0, tries: int = 8) -> Path | None:
    """Download one day's 1-minute candles (raw .bi5) unless cached. Returns the path, or None
    if the vendor has no file for that day (404 or empty)."""
    p = duka_path(root, instrument, day, side)
    if p.exists():
        return p
    url = f"{DUKA}/{instrument}/{day:%Y}/{day.month - 1:02d}/{day:%d}/{side}_candles_min_1.bi5"
    for i in range(tries):
        try:
            r = s.get(url, headers=UA, timeout=30)
        except requests.RequestException as e:
            wait = min(120, pause * 2 ** (i + 1))
            log.warning("dukascopy %s: %s; retry in %.0fs", day, type(e).__name__, wait)
            s.close()                                         # a dropped tunnel: start a fresh connection
            time.sleep(wait)
            continue
        if r.status_code == 200:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(r.content)
            tmp.replace(p)
            time.sleep(pause)
            return p
        if r.status_code == 404:
            time.sleep(pause)
            return None
        wait = min(120, pause * 2 ** (i + 2))                 # 429 / 503: the vendor's throttle
        time.sleep(wait)
    raise RuntimeError(f"dukascopy {day}: still failing after {tries} tries")


def duka_parse(path: Path, day: dt.date, point: float = 1000.0) -> pd.DataFrame:
    """bi5 candles: big-endian (seconds from 00:00 UTC, open, close, low, high, volume float)."""
    raw = path.read_bytes()
    if not raw:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    b = lzma.decompress(raw)
    a = np.frombuffer(b, dtype=np.dtype([("t", ">i4"), ("o", ">i4"), ("c", ">i4"), ("l", ">i4"), ("h", ">i4"),
                                         ("v", ">f4")]))
    t0 = pd.Timestamp(day, tz="UTC")
    idx = pd.DatetimeIndex(t0 + pd.to_timedelta(a["t"].astype("int64"), unit="s"), name="ts")
    df = pd.DataFrame({"open": a["o"] / point, "high": a["h"] / point, "low": a["l"] / point,
                       "close": a["c"] / point, "volume": a["v"].astype(float)}, index=idx)
    # flat zero-volume minutes are the vendor carrying the last price forward: no trading
    return df[df["volume"] > 0]


def duka_download(root: Path, start: dt.date, end: dt.date, instrument: str = "USA500IDXUSD",
                  pause: float = 2.0) -> dict:
    s = requests.Session()
    got = missing = 0
    failed: list[dt.date] = []
    todo = [d for d in sessions(max(start, DUKA_FIRST), end) if not duka_path(root, instrument, d).exists()]
    for rnd in range(3):                                      # failed days are retried in later rounds
        failed = []
        for d in todo:
            try:
                p = duka_fetch_day(s, root, instrument, d, pause=pause, tries=5)
            except RuntimeError as e:
                log.warning("%s; skipped for now", e)
                failed.append(d)
                continue
            got += p is not None
            missing += p is None
        todo = failed
        if not todo:
            break
    return {"days": got, "missing": missing, "failed": [str(d) for d in failed]}


def duka_bars(root: Path, start: dt.date, end: dt.date, instrument: str = "USA500IDXUSD") -> pd.DataFrame:
    parts = []
    for d in sessions(start, end):
        p = duka_path(root, instrument, d)
        if p.exists():
            parts.append(duka_parse(p, d))
    return pd.concat(parts).sort_index() if parts else pd.DataFrame()


# ------------------------------------------------------------------ HistData
HISTDATA = "https://www.histdata.com"


def histdata_download(folder: Path, year: int, month: int | None = None, pair: str = "SPXUSD") -> Path:
    """One year (or, for the current year, one month) of M1 ASCII bars from histdata.com: the page
    gives a form token, the zip comes from get.php. Cached: an existing csv is not fetched again."""
    import io
    import re
    import zipfile
    folder = Path(folder)
    name = f"DAT_ASCII_{pair}_M1_{year}{'' if month is None else f'{month:02d}'}.csv"
    out = folder / name
    if out.exists():
        return out
    s = requests.Session()
    s.headers.update(UA)
    ref = f"{HISTDATA}/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{pair.lower()}/{year}" + (
        f"/{month}" if month else "")
    page = s.get(ref, timeout=30)
    page.raise_for_status()
    tk = re.search(r'id="tk"\s+value="([^"]+)"', page.text)
    if not tk:
        raise RuntimeError(f"histdata: no form token on {ref}")
    data = {"tk": tk.group(1), "date": str(year), "datemonth": f"{year}{month:02d}" if month else str(year),
            "platform": "ASCII", "timeframe": "M1", "fxpair": pair}
    z = s.post(f"{HISTDATA}/get.php", data=data, timeout=180,
               headers={"Referer": ref, "Content-Type": "application/x-www-form-urlencoded"})
    if z.status_code != 200 or z.content[:2] != b"PK":
        raise RuntimeError(f"histdata {year}-{month}: HTTP {z.status_code}, not a zip")
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    csv = [n for n in zf.namelist() if n.endswith(".csv")][0]
    folder.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(zf.read(csv))
    tmp.replace(out)
    time.sleep(3)
    return out


# ------------------------------------------------------------------ HistData files
def histdata_bars(folder: Path) -> pd.DataFrame:
    """DAT_ASCII_SPXUSD_M1_YYYY.csv files: 'YYYYMMDD HHMMSS;open;high;low;close;volume', EST without DST."""
    parts = []
    for f in sorted(Path(folder).glob("DAT_ASCII_SPXUSD_M1_*.csv")):     # yearly and monthly files
        df = pd.read_csv(f, sep=";", header=None, names=["t", "open", "high", "low", "close", "volume"])
        ts = pd.to_datetime(df["t"], format="%Y%m%d %H%M%S") + pd.Timedelta(hours=5)     # fixed EST -> UTC
        df.index = pd.DatetimeIndex(ts, name="ts").tz_localize("UTC")
        parts.append(df[["open", "high", "low", "close", "volume"]])
    out = pd.concat(parts).sort_index()
    return out[~out.index.duplicated(keep="last")]


# ------------------------------------------------------------------ Oanda mirror
def oanda_bars(files: list[Path]) -> pd.DataFrame:
    """oanda-SPX500_USD-YYYY-M.csv: time (UTC), close, high, low, open, volume (tick count).
    UTC verified: the busiest minute is 14:30 UTC in January and 13:30 UTC in July (the 9:30 open)."""
    df = pd.concat([pd.read_csv(f) for f in files])
    df.index = pd.DatetimeIndex(pd.to_datetime(df["time"]), name="ts").tz_localize("UTC")
    df = df[["open", "high", "low", "close", "volume"]].sort_index()
    return df[~df.index.duplicated(keep="last")]


# ------------------------------------------------------------------ Yahoo
def yahoo_chart(symbol: str, interval: str, start: int | None = None, end: int | None = None,
                rng: str | None = None) -> pd.DataFrame:
    params = {"interval": interval, "includePrePost": "true", "events": "div,splits"}
    if rng:
        params["range"] = rng
    else:
        params.update(period1=start, period2=end)
    for host in ("query2", "query1"):
        for i in range(4):
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}", params=params,
                             headers=UA, timeout=30)
            if r.status_code == 200:
                res = r.json()["chart"]["result"][0]
                q = res["indicators"]["quote"][0]
                df = pd.DataFrame({k: q.get(k) for k in ("open", "high", "low", "close", "volume")},
                                  index=pd.DatetimeIndex(pd.to_datetime(res.get("timestamp", []), unit="s", utc=True),
                                                         name="ts"))
                if "adjclose" in res["indicators"]:
                    df["adjclose"] = res["indicators"]["adjclose"][0]["adjclose"]
                df.attrs["dividends"] = res.get("events", {}).get("dividends", {})
                return df.dropna(subset=["open", "close"])
            time.sleep(2 ** i)
    raise RuntimeError(f"yahoo {symbol} {interval}: no answer")


def yahoo_daily(symbol: str) -> pd.DataFrame:
    df = yahoo_chart(symbol, "1d", start=0, end=int(time.time()))     # range=max silently returns monthly bars
    divs = df.attrs.get("dividends", {})
    df.index = pd.DatetimeIndex([t.tz_convert(NY).date() for t in df.index], name="day")
    df.attrs["dividends"] = pd.Series({pd.Timestamp(int(k), unit="s", tz="UTC").tz_convert(NY).date(): v["amount"]
                                       for k, v in divs.items()}, dtype=float).sort_index()
    return df


def yahoo_spy_minutes(days: int = 29) -> pd.DataFrame:
    """Real SPY 1-minute bars for the last ~30 days (Yahoo serves 1m only that far back, 8 days a call)."""
    now = int(time.time())
    parts, a = [], now - days * 86400
    while a < now:
        b = min(a + 7 * 86400, now)
        parts.append(yahoo_chart("SPY", "1m", a, b))
        a = b
    df = pd.concat(parts).sort_index()
    return df[~df.index.duplicated(keep="last")]


# ------------------------------------------------------------------ proxy
def spy_equivalent(index_bars: pd.DataFrame, spy_daily: pd.DataFrame, spx_daily: pd.DataFrame) -> pd.DataFrame:
    """Scale index bars to SPY dollars with the previous session's SPY/S&P 500 close ratio."""
    ratio = (spy_daily["close"] / spx_daily["close"]).dropna().sort_index()
    prev = ratio.shift(1)
    days = pd.Index(index_bars.index.tz_convert(NY).date)
    f = prev.reindex(days).to_numpy()
    out = index_bars.copy()
    for c in ("open", "high", "low", "close"):
        out[c] = index_bars[c].to_numpy() * f
    return out[np.isfinite(f)]
