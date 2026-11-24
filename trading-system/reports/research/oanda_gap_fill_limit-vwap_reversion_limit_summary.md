# In-sample study: oanda (2009-11-02 .. 2019-12-31, 2558 sessions)

Every grid point under three cost scenarios; walk-forward selection on the unfavorable scenario.
Proxy data (S&P 500 index CFD scaled to SPY dollars). Not out-of-sample.

## gap_fill_limit

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"min_gap": 0.002, "max_gap": 0.005, "entry_minute": 0, "entry": "limit"}` | 816 | -10,973 | -0.95 | 11.4% | 18% | 0.75 | -0.433 |
| `{"min_gap": 0.002, "max_gap": 0.01, "entry_minute": 4, "entry": "limit"}` | 1230 | -19,615 | -1.08 | 20.6% | 26% | 0.78 | -0.393 |
| `{"min_gap": 0.002, "max_gap": 0.005, "entry_minute": 4, "entry": "limit"}` | 778 | -14,527 | -1.16 | 14.9% | 27% | 0.72 | -0.462 |
| `{"min_gap": 0.002, "max_gap": 0.01, "entry_minute": 14, "entry": "limit"}` | 1222 | -22,118 | -1.23 | 23.6% | 34% | 0.75 | -0.256 |
| `{"min_gap": 0.002, "max_gap": 0.005, "entry_minute": 14, "entry": "limit"}` | 762 | -16,086 | -1.23 | 16.6% | 35% | 0.70 | -0.259 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## vwap_reversion_limit

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"stretch_atr": 3.5, "rsi_extreme": 20.0, "stop_atr": 2.0, "entry": "limit"}` | 361 | -4,456 | -0.51 | 4.5% | 37% | 0.81 | -0.162 |
| `{"stretch_atr": 3.5, "rsi_extreme": 20.0, "stop_atr": 1.0, "entry": "limit"}` | 409 | -7,997 | -0.97 | 8.0% | 20% | 0.63 | -0.455 |
| `{"stretch_atr": 2.5, "rsi_extreme": 20.0, "stop_atr": 2.0, "entry": "limit"}` | 466 | -9,633 | -0.98 | 9.6% | 35% | 0.70 | -0.239 |
| `{"stretch_atr": 2.5, "rsi_extreme": 20.0, "stop_atr": 1.0, "entry": "limit"}` | 520 | -12,817 | -1.49 | 12.8% | 19% | 0.55 | -0.537 |
| `{"stretch_atr": 3.5, "rsi_extreme": 30.0, "stop_atr": 2.0, "entry": "limit"}` | 1908 | -29,157 | -1.73 | 29.2% | 36% | 0.73 | -0.212 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## Families after a Holm correction (walk-forward, unfavorable costs)

| family | p | Holm-adjusted p |
|---|---|---|
| gap_fill_limit | 1.000 | 1.000 |
| vwap_reversion_limit | 1.000 | 1.000 |
