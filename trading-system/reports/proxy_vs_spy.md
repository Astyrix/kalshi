# Proxy check: Dukascopy S&P 500 (SPY-scaled) vs real SPY (Yahoo), 9:30-11:00

22 sessions, 2026-10-26 .. 2026-11-24, 1,799 matched minutes.

| measure | value |
|---|---|
| correlation of 1-minute returns | 0.988 |
| correlation of 5-minute returns | 0.997 |
| proxy level vs SPY, median / 90th pct of abs gap (bp) | +0.2 / 5.0 |
| median ratio of 1-minute high-low ranges (proxy / SPY) | 1.00 |
| days where the first 15-minute range break has the same direction | 100% |
| median timing difference of that break when both agree (minutes) | 0 |

Limits: one month only (Yahoo keeps ~30 days of 1-minute bars); the proxy is an index CFD bid feed;
volume is not comparable. Strategy conclusions on the proxy must be confirmed on real SPY bars
(paper trading on Yahoo's live 1-minute bars) before any money is involved.
