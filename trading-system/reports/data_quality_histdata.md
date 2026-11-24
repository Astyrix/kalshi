# Data quality: histdata (SPY-equivalent proxy)

Period 2010-11-15 to 2018-12-31. Generated 2026-11-24 21:31 UTC.

| check | value |
|---|---|
| sessions expected | 2,045 |
| sessions empty | 4 |
| sessions usable | 1,118 |
| bars present | 737,445 |
| bars expected | 794,130 |
| missing in 9 30 11 00 | 13,045 |
| zero volume bars | 737,445 |
| bad ohlc bars | 0 |
| extreme moves | 258 |
| duplicates | 0 |
| nonpositive or nan | 0 |
| bars outside sessions | 0 |

## Sessions with the most missing bars in 9:30-11:00

| day | missing (of 90) | bars present / expected | zero-volume | bad OHLC | extreme moves |
|---|---|---|---|---|---|
| 2014-06-02 | 90 | 0 / 390 | 0 | 0 | 0 |
| 2017-02-21 | 90 | 0 / 390 | 0 | 0 | 0 |
| 2017-02-22 | 90 | 0 / 390 | 0 | 0 | 0 |
| 2017-02-23 | 90 | 0 / 390 | 0 | 0 | 0 |
| 2017-02-24 | 90 | 0 / 390 | 0 | 0 | 0 |
| 2017-10-03 | 45 | 248 / 390 | 248 | 0 | 0 |
| 2010-12-29 | 41 | 157 / 390 | 157 | 0 | 0 |
| 2011-05-02 | 40 | 311 / 390 | 311 | 0 | 0 |
| 2014-05-01 | 40 | 338 / 390 | 338 | 0 | 0 |
| 2017-09-20 | 39 | 308 / 390 | 308 | 0 | 0 |
| 2017-10-16 | 39 | 262 / 390 | 262 | 0 | 0 |
| 2017-10-12 | 38 | 260 / 390 | 260 | 0 | 0 |
| 2014-06-09 | 37 | 278 / 390 | 278 | 0 | 0 |
| 2014-06-10 | 37 | 285 / 390 | 285 | 0 | 0 |
| 2017-10-10 | 37 | 332 / 390 | 332 | 0 | 0 |

## Sessions with no data (4)

2017-02-21, 2017-02-22, 2017-02-23, 2017-02-24

## Notes and limitations

- Index CFD prices scaled to SPY dollars with the previous session's SPY / S&P 500 close ratio.
- Volume is tick count (Dukascopy) or absent (HistData): zero-volume counts are expected and not errors.
- HistData timestamps are EST all year (no DST) and were shifted by +5 h to UTC before conversion to New York time.
