"""Real SPY option chains from Yahoo Finance: free, real bid/ask, about 15 minutes DELAYED.

Access: Yahoo's chain endpoint needs the anonymous session cookie that fc.yahoo.com sets and a
"crumb" token tied to it (the same public flow any browser uses); both are refreshed on 401.

Delay: the underlying quote in the response is real time, the option quotes are not. Each
snapshot's quote time is estimated as the latest trade time among the near-the-money contracts
of the nearest expiry (0DTE SPY options trade every second, so this is the quote's real age).
On 2026-11-25 put-call parity of the mids matched SPY 15 minutes earlier, and the latest trade
was 15:02 old. Anything that prices fills from these quotes must use `quote_ts`, never the
fetch time.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (research; tradesys)"}
CHAIN = "https://query2.finance.yahoo.com/v7/finance/options/{sym}"
CRUMB = "https://query2.finance.yahoo.com/v1/test/getcrumb"


class YahooOptions:
    def __init__(self, symbol: str = "SPY", cache: Path | None = None):
        """cache: optional JSON file keeping the anonymous cookie and crumb between runs, so a
        restart does not log in again (the crumb endpoint rate-limits repeated logins)."""
        self.symbol, self.s, self.crumb, self.cache = symbol, None, None, cache
        if cache and cache.exists():
            try:
                c = json.loads(cache.read_text())
                s = requests.Session()
                s.headers.update(UA)
                s.cookies.update(c["cookies"])
                self.s, self.crumb = s, c["crumb"]
            except Exception:
                self.s = None

    def _auth(self) -> None:
        s = requests.Session()
        s.headers.update(UA)
        s.get("https://fc.yahoo.com/", timeout=20)          # sets the anonymous A3 cookie (the page itself 404s)
        c = s.get(CRUMB, timeout=20)
        if c.status_code != 200 or not c.text or len(c.text) > 64:
            raise RuntimeError(f"yahoo crumb: HTTP {c.status_code}")
        self.s, self.crumb = s, c.text.strip()
        if self.cache:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps({"cookies": s.cookies.get_dict(), "crumb": self.crumb}))

    def _get(self, params: dict) -> dict:
        last = None
        for attempt in range(4):
            if self.s is None:
                try:
                    self._auth()
                except (RuntimeError, requests.RequestException) as e:
                    last = str(e)
                    time.sleep(10 * (attempt + 1))
                    continue
            r = self.s.get(CHAIN.format(sym=self.symbol), params={**params, "crumb": self.crumb}, timeout=20)
            if r.status_code in (401, 403):
                self.s, last = None, f"HTTP {r.status_code}"
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            res = r.json()["optionChain"]["result"]
            if not res:
                raise RuntimeError("yahoo options: empty result")
            return res[0]
        raise RuntimeError(f"yahoo options failed: {last}")

    def expiries(self) -> tuple[list[dt.date], dict]:
        res = self._get({})
        return [pd.Timestamp(x, unit="s").date() for x in res["expirationDates"]], res

    def chain(self, expiry: dt.date, band: float = 0.05, res: dict | None = None) -> pd.DataFrame:
        """All calls and puts of one expiry with strikes within +-band of the underlying."""
        if res is None or pd.Timestamp(res["options"][0]["expirationDate"], unit="s").date() != expiry:
            res = self._get({"date": int(pd.Timestamp(expiry).tz_localize("UTC").timestamp())})
        fetch = pd.Timestamp.now(tz="UTC")
        q = res["quote"]
        S = float(q["regularMarketPrice"])
        rows = []
        for right, key in (("C", "calls"), ("P", "puts")):
            for o in res["options"][0].get(key, []):
                k = float(o["strike"])
                if abs(k / S - 1) > band:
                    continue
                rows.append({"fetch_ts": fetch, "under_px_rt": S,
                             "under_ts": pd.Timestamp(q["regularMarketTime"], unit="s", tz="UTC"),
                             "expiry": expiry, "right": right, "strike": k,
                             "bid": float(o.get("bid") or np.nan), "ask": float(o.get("ask") or np.nan),
                             "last": float(o.get("lastPrice") or np.nan), "iv": float(o.get("impliedVolatility") or np.nan),
                             "volume": float(o.get("volume") or 0), "oi": float(o.get("openInterest") or 0),
                             "last_trade": pd.Timestamp(o["lastTradeDate"], unit="s", tz="UTC") if o.get("lastTradeDate") else pd.NaT,
                             "symbol": o.get("contractSymbol")})
        return pd.DataFrame(rows)


def quote_time(ch: pd.DataFrame, near: float = 0.01) -> pd.Timestamp:
    """Estimated time of the quotes in a nearest-expiry snapshot: latest trade near the money."""
    S = ch["under_px_rt"].iloc[0]
    nm = ch[(ch["strike"] / S - 1).abs() <= near]
    return (nm if len(nm) else ch)["last_trade"].max()


def snapshot(yo: YahooOptions, today: dt.date, band: float = 0.05) -> pd.DataFrame:
    """One snapshot: the nearest expiry (0DTE on a listed day), the next one, and the first at
    least 7 calendar days out. Every row carries the snapshot's estimated quote time."""
    exps, res0 = yo.expiries()
    exps = [e for e in exps if e >= today]
    pick = exps[:2] + [e for e in exps if (e - today).days >= 7][:1]
    pick = list(dict.fromkeys(pick))
    parts = [yo.chain(e, band, res0 if i == 0 else None) for i, e in enumerate(pick)]
    out = pd.concat([p for p in parts if len(p)], ignore_index=True)
    qt = quote_time(parts[0]) if len(parts[0]) else pd.NaT
    out["quote_ts"] = qt
    out["delay_s"] = (out["fetch_ts"] - qt).dt.total_seconds()
    return out
