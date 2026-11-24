"""Local Parquet cache: one file per vendor/dataset/schema/symbol/month, plus a manifest.

Layout: data/raw/<vendor>/<dataset>/<schema>/<symbol>/<YYYY-MM>.parquet
A month counts as complete only once it is written with its manifest entry and the month has
ended (the current month is always re-fetched from scratch).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

VENDOR = "databento"


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


def _dir(root: Path, dataset: str, schema: str, symbol: str, vendor: str = VENDOR) -> Path:
    return Path(root) / "raw" / vendor / dataset / schema / symbol


def _manifest(d: Path) -> dict[str, Any]:
    p = d / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else {}


def has_month(root: Path, dataset: str, schema: str, symbol: str, a: pd.Timestamp, b: pd.Timestamp,
              vendor: str = VENDOR) -> bool:
    d = _dir(root, dataset, schema, symbol, vendor)
    m = _manifest(d).get(a.strftime("%Y-%m"))
    if not m or not (d / f"{a:%Y-%m}.parquet").exists():
        return False
    # a month fetched before it ended is incomplete
    return pd.Timestamp(m["fetched_utc"]) >= b and pd.Timestamp(m["end"]) >= b


def write_month(root: Path, dataset: str, schema: str, symbol: str, a: pd.Timestamp, b: pd.Timestamp,
                bars: pd.DataFrame, meta: dict[str, Any], vendor: str = VENDOR) -> Path:
    d = _dir(root, dataset, schema, symbol, vendor)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{a:%Y-%m}.parquet"
    tmp = path.with_suffix(".tmp")
    pq.write_table(pa.Table.from_pandas(bars.reset_index(), preserve_index=False), tmp, compression="zstd")
    tmp.replace(path)                                   # atomic: a crash never leaves a half file
    man = _manifest(d)
    man[a.strftime("%Y-%m")] = dict(meta, start=a.isoformat(), end=b.isoformat())
    (d / "manifest.json").write_text(json.dumps(man, indent=1, default=str))
    return path


def load_bars(root: Path, dataset: str, symbol: str, start: str | None = None, end: str | None = None,
              schema: str = "ohlcv-1m", vendor: str = VENDOR) -> pd.DataFrame:
    """All cached months between start and end (UTC, end exclusive), one DataFrame indexed by ts."""
    d = _dir(root, dataset, schema, symbol, vendor)
    files = sorted(d.glob("*.parquet"))
    if start:
        files = [f for f in files if f.stem >= _utc(start).strftime("%Y-%m")]
    if end:
        files = [f for f in files if f.stem <= _utc(end).strftime("%Y-%m")]
    if not files:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], tz="UTC", name="ts"))
    df = pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)
    df = df.set_index(pd.DatetimeIndex(df["ts"], name="ts")).drop(columns="ts")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if start:
        df = df[df.index >= _utc(start)]
    if end:
        df = df[df.index < _utc(end)]
    return df
