# In-sample study: oanda (2009-11-02 .. 2019-12-31, 2558 sessions)

Every grid point under three cost scenarios; walk-forward selection on the unfavorable scenario.
Proxy data (S&P 500 index CFD scaled to SPY dollars). Not out-of-sample.

## open_drive

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"minute": 4, "k": 0.2, "max_hold_min": 10}` | 119 | 893 | 0.20 | 1.2% | 52% | 1.16 | +0.040 |
| `{"minute": 4, "k": 0.2, "max_hold_min": 20}` | 119 | 293 | 0.05 | 2.3% | 47% | 1.04 | +0.015 |
| `{"minute": 9, "k": 0.2, "max_hold_min": 20}` | 366 | -1,115 | -0.12 | 4.2% | 48% | 0.95 | -0.025 |
| `{"minute": 4, "k": 0.1, "max_hold_min": 20}` | 724 | -3,620 | -0.25 | 7.1% | 44% | 0.93 | -0.058 |
| `{"minute": 9, "k": 0.2, "max_hold_min": 10}` | 366 | -1,890 | -0.26 | 3.9% | 45% | 0.89 | -0.034 |

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
| open_drive | 1.000 | 1.000 |
