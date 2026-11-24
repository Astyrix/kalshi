"""Resumable, throttled download of Dukascopy USA500.IDX/USD 1-minute candles (raw .bi5 cache).
Usage: python scripts/fetch_dukascopy.py [START] [END]   (defaults 2011-09-19 .. yesterday)"""
import datetime as dt
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tradesys.data_ingestion.free_sources import duka_download  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
a = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date(2011, 9, 19)
b = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else dt.date.today() - dt.timedelta(days=1)
print(duka_download(ROOT / "data", a, b), flush=True)
