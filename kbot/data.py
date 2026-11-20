"""Market data from maxycharts.com (a free relay of Kalshi + CF Benchmarks BRTI).

Endpoints are the ones the maxycharts.com web app itself uses; they are not a
documented public API and may change. Every call is rate-limited, and
historical windows are cached on disk so a backtest only downloads once.
"""
from __future__ import annotations

import gzip
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://maxycharts.com"
KALSHI = f"{BASE}/api/kalshi"

SERIES = {
    "BTC": "KXBTC15M", "ETH": "KXETH15M", "SOL": "KXSOL15M", "XRP": "KXXRP15M",
    "DOGE": "KXDOGE15M", "BNB": "KXBNB15M", "HYPE": "KXHYPE15M",
}


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


@dataclass
class Book:
    """Top of book in dollars per YES contract. NO prices are 1 - YES."""
    yes_bid: float | None
    yes_ask: float | None
    yes_bid_size: float = 0.0
    yes_ask_size: float = 0.0
    yes_bid_top: float = 0.0        # contracts resting at the best YES bid
    yes_ask_top: float = 0.0        # contracts offered at the best YES ask (= best NO bid)

    def top_ask_size(self, side: str) -> float:
        """Contracts you could buy at the best ask right now."""
        return self.yes_ask_top if side == "UP" else self.yes_bid_top

    @property
    def no_bid(self):
        return None if self.yes_ask is None else round(1 - self.yes_ask, 4)

    @property
    def no_ask(self):
        return None if self.yes_bid is None else round(1 - self.yes_bid, 4)

    def bid(self, side: str):
        return self.yes_bid if side == "UP" else self.no_bid

    def ask(self, side: str):
        return self.yes_ask if side == "UP" else self.no_ask

    @property
    def imbalance(self) -> float:
        """+1 = all resting size on the YES bid side, -1 = all on the NO side."""
        tot = self.yes_bid_size + self.yes_ask_size
        return 0.0 if tot <= 0 else (self.yes_bid_size - self.yes_ask_size) / tot


@dataclass
class Window:
    """One settled (or live) 15-minute market and its data."""
    event: str
    ticker: str
    strike: float
    open_ts: int
    close_ts: int
    result: str | None = None            # "yes" / "no" once settled
    settle_value: float | None = None
    index: list[tuple[int, float]] = field(default_factory=list)   # BRTI per second
    # per-minute Kalshi quotes: (end_ts, yes_bid_close, yes_ask_close, volume)
    quotes: list[tuple[int, float | None, float | None, float]] = field(default_factory=list)

    def to_json(self) -> dict:
        return self.__dict__

    @classmethod
    def from_json(cls, d: dict) -> "Window":
        w = cls(**d)
        w.index = [tuple(p) for p in w.index]
        w.quotes = [tuple(q) for q in w.quotes]
        return w


class MaxyClient:
    def __init__(self, coin: str = "BTC", min_interval: float = 0.3,
                 cache_dir: str | Path = "data/cache", timeout: float = 10.0):
        self.coin = coin.upper()
        self.series = SERIES[self.coin]
        self.min_interval = min_interval
        self.cache = Path(cache_dir) / self.coin
        self.cache.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"accept": "application/json",
                               "user-agent": "kbot-research/0.1"})
        self._lock = threading.Lock()
        self._last = 0.0

    # ---- raw HTTP -------------------------------------------------------
    def _get(self, url: str, params: dict | None = None, retries: int = 4):
        for attempt in range(retries):
            with self._lock:
                wait = self.min_interval - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            try:
                r = self.s.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"{r.status_code}")
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError):
                if attempt == retries - 1:
                    raise
                time.sleep(1.5 * 2 ** attempt)

    # ---- live -----------------------------------------------------------
    def current_market(self) -> dict:
        d = self._get(f"{KALSHI}/events", {"series_ticker": self.series, "status": "open",
                                           "limit": 1, "with_nested_markets": "true"})
        ev = d["events"][0]
        return ev["markets"][0]

    def book(self, ticker: str, depth: int = 10) -> Book:
        d = self._get(f"{KALSHI}/markets/{ticker}/orderbook", {"depth": depth})["orderbook_fp"]
        yes = [(float(p), float(q)) for p, q in d.get("yes_dollars") or []]
        no = [(float(p), float(q)) for p, q in d.get("no_dollars") or []]
        yes_bid = max((p for p, _ in yes), default=None)
        no_bid = max((p for p, _ in no), default=None)
        return Book(
            yes_bid=yes_bid,
            yes_ask=None if no_bid is None else round(1 - no_bid, 4),
            yes_bid_size=sum(q for _, q in yes),
            yes_ask_size=sum(q for _, q in no),
            yes_bid_top=sum(q for p, q in yes if p == yes_bid),
            yes_ask_top=sum(q for p, q in no if p == no_bid),
        )

    def index_series(self, event: str, minutes: int = 20) -> list[tuple[int, float]]:
        d = self._get(f"{BASE}/api/live/{event}", {"minutes": minutes})
        return [(int(t), float(v)) for t, v in d["points"]]

    # ---- history --------------------------------------------------------
    def settled_markets(self, start_ts: int, end_ts: int) -> list[dict]:
        """Settled markets with close_time in [start_ts, end_ts], newest first."""
        out, cursor = [], None
        while True:
            params = {"series_ticker": self.series, "status": "settled", "limit": 200,
                      "with_nested_markets": "true", "max_close_ts": end_ts}
            if cursor:
                params["cursor"] = cursor
            d = self._get(f"{KALSHI}/events", params)
            evs = d.get("events") or []
            stop = False
            for ev in evs:
                for m in ev.get("markets") or []:
                    c = _ts(m["close_time"])
                    if c < start_ts:
                        stop = True
                    elif c <= end_ts:
                        out.append(m)
            cursor = d.get("cursor")
            if stop or not cursor or not evs:
                return out

    def market(self, ticker: str) -> dict | None:
        """Look up one market; Kalshi moves ones older than ~2 months to /historical."""
        for path in (f"{KALSHI}/markets/{ticker}", f"{KALSHI}/historical/markets/{ticker}"):
            try:
                m = self._get(path, retries=2).get("market")
            except Exception:
                continue
            if m:
                if "historical" in path:
                    m["_historical"] = True
                return m
        return None

    def older_markets(self, start_ts: int, end_ts: int) -> list[dict]:
        """Markets the events listing no longer returns (it stops after ~2 months).

        Tickers are deterministic from the close time in New York time, e.g.
        KXBTC15M-26NOV171615-15 closes 16:15 ET. Each one costs a lookup.
        Already-cached windows are not looked up again.
        """
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        out = []
        t = end_ts - end_ts % 900
        while t >= start_ts:
            ct = datetime.fromtimestamp(t, timezone.utc).astimezone(ny)
            ev = f"{self.series}-{ct.strftime('%y%b%d%H%M').upper()}"
            ticker = f"{ev}-{ct.strftime('%M')}"
            if (self.cache / f"{ticker}.json.gz").exists():
                out.append({"ticker": ticker, "_cached": True})
            else:
                out.append({"ticker": ticker, "event_ticker": ev, "_lookup": True})
            t -= 900
        return out

    def window(self, m: dict) -> Window | None:
        """Full history for one settled market (cached on disk)."""
        path = self.cache / f"{m['ticker']}.json.gz"
        if path.exists():
            with gzip.open(path, "rt") as fh:
                return Window.from_json(json.load(fh))
        if m.get("_cached"):
            return None
        if m.get("_lookup"):
            m = self.market(m["ticker"])
            if not m or m.get("result") not in ("yes", "no"):
                return None
        open_ts, close_ts = _ts(m["open_time"]), _ts(m["close_time"])
        strike = _f(m.get("floor_strike"))
        if strike is None:
            return None
        live = self._get(f"{BASE}/api/live/{m['event_ticker']}", {"minutes": 20})
        cparams = {"start_ts": open_ts, "end_ts": close_ts, "period_interval": 1}
        if m.get("_historical"):
            cs = self._get(f"{KALSHI}/historical/markets/{m['ticker']}/candlesticks", cparams)
        else:
            cs = self._get(f"{KALSHI}/series/{self.series}/markets/{m['ticker']}/candlesticks", cparams)
        save_px(self, m["ticker"], cs)
        quotes = []
        for c in cs.get("candlesticks") or []:
            yb, ya = c.get("yes_bid") or {}, c.get("yes_ask") or {}
            # live endpoint uses *_dollars / volume_fp; historical uses close / volume
            quotes.append((int(c["end_period_ts"]),
                           _f(yb.get("close_dollars", yb.get("close"))),
                           _f(ya.get("close_dollars", ya.get("close"))),
                           _f(c.get("volume_fp", c.get("volume"))) or 0.0))
        w = Window(event=m["event_ticker"], ticker=m["ticker"], strike=strike,
                   open_ts=open_ts, close_ts=close_ts, result=m.get("result"),
                   settle_value=_f(m.get("expiration_value")),
                   index=[(int(t), float(v)) for t, v in live.get("points") or []],
                   quotes=quotes)
        with gzip.open(path, "wt") as fh:
            json.dump(w.to_json(), fh)
        return w


# ---- order flow (who is buying vs selling) -------------------------------------
def flow_path(client: "MaxyClient", ticker: str) -> Path:
    d = client.cache.parent / f"{client.coin}-flow"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{ticker}.json.gz"


def load_flow(client: "MaxyClient", ticker: str):
    p = flow_path(client, ticker)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt") as fh:
            return json.load(fh)
    except (OSError, EOFError, ValueError):
        return None


def fetch_flow(client: "MaxyClient", w: "Window", bucket: int = 10, max_pages: int = 80):
    """Kalshi trade tape for one window, summed into `bucket`-second buckets:
    [bucket_ts, yes_taker_contracts, no_taker_contracts, trades, vwap_yes].
    "yes taker" = someone paid up to buy YES (UP); "no taker" = paid up for NO."""
    got = load_flow(client, w.ticker)
    if got is not None:
        return got
    hist = w.close_ts < time.time() - 55 * 86400
    url = f"{KALSHI}/historical/trades" if hist else f"{KALSHI}/markets/trades"
    agg, cursor = {}, None
    for _ in range(max_pages):
        params = {"ticker": w.ticker, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        d = client._get(url, params)
        tr = d.get("trades") or []
        if not tr and not hist and not cursor:      # moved to the archive
            url, hist = f"{KALSHI}/historical/trades", True
            continue
        for t in tr:
            ts = _ts(t["created_time"])
            b = ts - ts % bucket
            n = float(t.get("count_fp") or t.get("count") or 0)
            yp = float(t.get("yes_price_dollars") or t.get("yes_price") or 0)
            a = agg.setdefault(b, [b, 0.0, 0.0, 0, 0.0])
            a[1 if t.get("taker_side") == "yes" else 2] += n
            a[3] += 1
            a[4] += n * yp
        cursor = d.get("cursor")
        if not cursor or not tr:
            break
    rows = []
    for b in sorted(agg):
        r = agg[b]
        vol = r[1] + r[2]
        rows.append([r[0], round(r[1], 2), round(r[2], 2), r[3], round(r[4] / vol, 4) if vol else None])
    with gzip.open(flow_path(client, w.ticker), "wt") as fh:
        json.dump(rows, fh)
    return rows


# ---- real traded prices per minute ------------------------------------------------
def px_path(client: "MaxyClient", ticker: str) -> Path:
    d = client.cache.parent / f"{client.coin}-px"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{ticker}.json.gz"


def save_px(client, ticker, cs):
    """[end_ts, open, high, low, close, volume] of YES trade prices per minute
    (None when nothing traded). Unlike bid/ask candles, these all happened."""
    rows = []
    for c in cs.get("candlesticks") or []:
        pr = c.get("price") or {}
        g = lambda k: _f(pr.get(f"{k}_dollars", pr.get(k)))
        vol = _f(c.get("volume_fp", c.get("volume"))) or 0.0
        rows.append([int(c["end_period_ts"]), g("open"), g("high"), g("low"), g("close"), vol])
    with gzip.open(px_path(client, ticker), "wt") as fh:
        json.dump(rows, fh)
    return rows


def load_px(client, ticker):
    p = px_path(client, ticker)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt") as fh:
            return json.load(fh)
    except (OSError, EOFError, ValueError):
        return None


def fetch_px(client, w: "Window"):
    """Download the traded-price candles for a cached window (one request)."""
    got = load_px(client, w.ticker)
    if got is not None:
        return got
    p = {"start_ts": w.open_ts, "end_ts": w.close_ts, "period_interval": 1}
    old = w.close_ts < time.time() - 55 * 86400
    urls = [f"{KALSHI}/historical/markets/{w.ticker}/candlesticks",
            f"{KALSHI}/series/{client.series}/markets/{w.ticker}/candlesticks"]
    for url in (urls if old else urls[::-1]):
        try:
            cs = client._get(url, p, retries=2)
        except Exception:
            continue
        if cs.get("candlesticks"):
            return save_px(client, w.ticker, cs)
    return None
