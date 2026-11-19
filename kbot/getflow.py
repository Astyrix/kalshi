"""Download order flow (trade tape) for the cached windows of the last N days.

  python -m kbot.getflow --days 7
"""
import argparse
from concurrent.futures import ThreadPoolExecutor

from .data import MaxyClient, fetch_flow, load_flow
from .sweep import load_cached


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", default="BTC")
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--rate", type=float, default=0.25)
    a = ap.parse_args(argv)
    c = MaxyClient(a.coin, min_interval=a.rate)
    ws = [w for w in load_cached(a.coin, a.days) if load_flow(c, w.ticker) is None]
    ws.reverse()                       # newest first
    print(f"{len(ws)} windows need flow data", flush=True)

    def one(w):
        try:
            fetch_flow(c, w)
            return True
        except Exception as e:
            print(f"  skip {w.ticker}: {e}", flush=True)
            return False

    with ThreadPoolExecutor(3) as ex:
        for i, _ in enumerate(ex.map(one, ws), 1):
            if i % 50 == 0:
                print(f"  {i}/{len(ws)}", flush=True)


if __name__ == "__main__":
    main()
