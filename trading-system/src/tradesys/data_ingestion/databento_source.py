"""Databento historical data: coverage, cost quotes and guarded, incremental downloads.

Spending money needs two keys: the environment ceiling (TRADESYS_MAX_SPEND_USD, default 0)
and an explicit per-command approval (`approve_usd`). A download runs only if Databento's own
quote (metadata.get_cost, which is not billed) is at or under both. Nothing here ever buys a
subscription or license.

The API key is read from the environment (DATABENTO_API_KEY), never from source code. In the
Claude cloud environment the key is injected by the network proxy; there the variable may hold
a placeholder.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from . import store

log = logging.getLogger(__name__)
PLACEHOLDER_KEY = "db-" + "X" * 29          # the format the client expects; the proxy swaps the real key in


class SpendNotApproved(RuntimeError):
    pass


def _client():
    import databento as db
    key = os.environ.get("DATABENTO_API_KEY") or PLACEHOLDER_KEY
    return db.Historical(key)


def retry(fn: Callable[[], Any], tries: int = 5, base: float = 2.0) -> Any:
    """Call fn with exponential backoff on network errors, 429 and 5xx. Auth and request errors raise."""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:                    # databento raises BentoHttpError subclasses
            status = getattr(e, "http_status", None) or getattr(e, "status_code", None)
            if status in (400, 401, 403, 404, 422) or i == tries - 1:
                raise
            wait = base ** i
            log.warning("databento call failed (%s, status %s); retry %d in %.0fs", type(e).__name__, status, i + 1, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def coverage(datasets: list[str]) -> dict[str, Any]:
    """{dataset: {'start':..., 'end':..., 'schemas': [...]}} straight from Databento's metadata."""
    c = _client()
    out = {}
    for ds in datasets:
        try:
            rng = retry(lambda: c.metadata.get_dataset_range(dataset=ds))
            schemas = retry(lambda: c.metadata.list_schemas(dataset=ds))
            out[ds] = {"range": rng, "schemas": schemas}
        except Exception as e:
            out[ds] = {"error": f"{type(e).__name__}: {e}"}
    return out


@dataclass(frozen=True)
class Request:
    dataset: str
    schema: str
    symbols: tuple[str, ...]
    start: str                     # ISO date or datetime (UTC), inclusive
    end: str                       # exclusive
    stype_in: str = "raw_symbol"

    def kw(self) -> dict[str, Any]:
        return dict(dataset=self.dataset, schema=self.schema, symbols=list(self.symbols),
                    start=self.start, end=self.end, stype_in=self.stype_in)


def quote(req: Request) -> dict[str, float]:
    """Databento's own estimate for a request: cost in USD and billable size in bytes."""
    c = _client()
    cost = float(retry(lambda: c.metadata.get_cost(**req.kw())))
    size = int(retry(lambda: c.metadata.get_billable_size(**req.kw())))
    return {"usd": cost, "bytes": size}


def _ceiling(approve_usd: float) -> float:
    env = float(os.environ.get("TRADESYS_MAX_SPEND_USD", "0") or 0)
    return min(env, float(approve_usd))


def month_chunks(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """[start, end) split at month boundaries (UTC)."""
    out, a = [], start
    while a < end:
        b = min((a + pd.offsets.MonthBegin(1)).normalize(), end)
        out.append((a, b))
        a = b
    return out


def fetch_bars(dataset: str, symbol: str, start: str, end: str, approve_usd: float, root: Path,
               schema: str = "ohlcv-1m", dry_run: bool = False) -> dict[str, Any]:
    """Download missing months of bars to Parquet. Quotes the total first and refuses
    if it exceeds the approved ceiling. Returns a summary (cost, months fetched/skipped)."""
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    todo = [(a, b) for a, b in month_chunks(s, e) if not store.has_month(root, dataset, schema, symbol, a, b)]
    summary: dict[str, Any] = {"dataset": dataset, "schema": schema, "symbol": symbol, "months_missing": len(todo)}
    if not todo:
        summary.update(usd=0.0, fetched=0)
        return summary
    reqs = [Request(dataset, schema, (symbol,), a.isoformat(), b.isoformat()) for a, b in todo]
    quotes = [quote(r) for r in reqs]
    total = sum(q["usd"] for q in quotes)
    summary.update(usd=round(total, 4), bytes=sum(q["bytes"] for q in quotes))
    if dry_run:
        return summary
    if total > _ceiling(approve_usd) + 1e-9:
        raise SpendNotApproved(f"quoted ${total:.2f} for {len(reqs)} month(s) of {dataset} {schema} {symbol}; "
                               f"approved ceiling is ${_ceiling(approve_usd):.2f} (TRADESYS_MAX_SPEND_USD and "
                               f"--approve-usd must both cover it)")
    c = _client()
    fetched = 0
    for r, q, (a, b) in zip(reqs, quotes, todo):
        data = retry(lambda: c.timeseries.get_range(**r.kw()))
        df = data.to_df()
        bars = normalize_ohlcv(df)
        store.write_month(root, dataset, schema, symbol, a, b, bars,
                          meta={"request": r.kw(), "quoted_usd": q["usd"], "rows": len(bars),
                                "fetched_utc": pd.Timestamp.now(tz="UTC").isoformat()})
        fetched += 1
        log.info("%s %s %s %s: %d bars ($%.4f)", dataset, schema, symbol, a.strftime("%Y-%m"), len(bars), q["usd"])
    summary["fetched"] = fetched
    return summary


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Databento ohlcv DataFrame -> ts (UTC bar start), open, high, low, close, volume; sorted, deduplicated."""
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], tz="UTC", name="ts"))
    out = df[["open", "high", "low", "close", "volume"]].copy()
    out.index = pd.DatetimeIndex(df.index, name="ts")
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out["volume"] = out["volume"].astype("int64")
    return out


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=str))
