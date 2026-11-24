# In-sample study: oanda (2009-11-02 .. 2019-12-31, 2558 sessions)

Every grid point under three cost scenarios; walk-forward selection on the unfavorable scenario.
Proxy data (S&P 500 index CFD scaled to SPY dollars). Not out-of-sample.

## ml_direction

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"minute": 4, "model": "logit", "margin": 0.05, "key": "oanda"}` | 920 | -10,389 | -0.73 | 14.0% | 47% | 0.82 | -0.050 |
| `{"minute": 4, "model": "lgbm", "margin": 0.05, "key": "oanda"}` | 1121 | -12,711 | -0.85 | 14.6% | 48% | 0.81 | -0.051 |
| `{"minute": 29, "model": "lgbm", "margin": 0.05, "key": "oanda"}` | 1075 | -10,892 | -0.94 | 12.0% | 47% | 0.79 | -0.045 |
| `{"minute": 29, "model": "logit", "margin": 0.05, "key": "oanda"}` | 618 | -8,841 | -0.95 | 10.2% | 44% | 0.73 | -0.063 |
| `{"minute": 29, "model": "lgbm", "margin": 0.02, "key": "oanda"}` | 1729 | -14,833 | -1.01 | 15.2% | 47% | 0.82 | -0.039 |

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
| ml_direction | 1.000 | 1.000 |
