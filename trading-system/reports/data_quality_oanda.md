# Data quality: oanda (SPY-equivalent proxy)

Period 2009-11-02 to 2020-05-14. Generated 2026-11-24 22:10 UTC.

| check | value |
|---|---|
| sessions expected | 2,651 |
| sessions empty | 0 |
| sessions usable | 2,359 |
| bars present | 965,948 |
| bars expected | 1,029,570 |
| missing in 9 30 11 00 | 3,751 |
| zero volume bars | 0 |
| bad ohlc bars | 0 |
| extreme moves | 517 |
| duplicates | 0 |
| nonpositive or nan | 0 |
| bars outside sessions | 0 |

## Sessions with the most missing bars in 9:30-11:00

| day | missing (of 90) | bars present / expected | zero-volume | bad OHLC | extreme moves |
|---|---|---|---|---|---|
| 2014-02-27 | 90 | 274 / 390 | 0 | 0 | 1 |
| 2020-05-14 | 90 | 0 / 390 | 0 | 0 | 0 |
| 2017-10-17 | 36 | 177 / 390 | 0 | 0 | 0 |
| 2012-03-26 | 31 | 223 / 390 | 0 | 0 | 0 |
| 2016-12-23 | 31 | 180 / 390 | 0 | 0 | 0 |
| 2017-11-24 | 31 | 114 / 210 | 0 | 0 | 0 |
| 2016-12-27 | 30 | 183 / 390 | 0 | 0 | 0 |
| 2017-12-27 | 30 | 232 / 390 | 0 | 0 | 0 |
| 2014-12-26 | 29 | 227 / 390 | 0 | 0 | 0 |
| 2017-10-11 | 29 | 209 / 390 | 0 | 0 | 0 |
| 2012-03-20 | 28 | 285 / 390 | 0 | 0 | 0 |
| 2012-03-16 | 26 | 249 / 390 | 0 | 0 | 0 |
| 2017-08-07 | 26 | 156 / 390 | 0 | 0 | 0 |
| 2017-10-13 | 25 | 191 / 390 | 0 | 0 | 0 |
| 2012-03-21 | 24 | 245 / 390 | 0 | 0 | 0 |

## Notes and limitations

- Index CFD prices scaled to SPY dollars with the previous session's SPY / S&P 500 close ratio.
- Volume is tick count (Dukascopy) or absent (HistData): zero-volume counts are expected and not errors.
- HistData timestamps are EST all year (no DST) and were shifted by +5 h to UTC before conversion to New York time.
