# In-sample study: oanda (2009-11-02 .. 2019-12-31, 2558 sessions)

Every grid point under three cost scenarios; walk-forward selection on the unfavorable scenario.
Proxy data (S&P 500 index CFD scaled to SPY dollars). Not out-of-sample.

## event_long

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"event": "fomc_eve"}` | 79 | 352 | 0.17 | 1.0% | 46% | 1.16 | +0.018 |
| `{"event": "fomc"}` | 79 | -1,078 | -0.63 | 1.2% | 35% | 0.55 | -0.056 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 32 | -110 | -0.09 | [-0.80, 0.52] | 0.588 |
| unfavorable | 2264 | 32 | -234 | -0.19 | [-0.88, 0.43] | 0.684 |
| extreme | 2264 | 32 | -576 | -0.47 | [-1.12, 0.18] | 0.900 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +551, 2015: -38, 2016: -381, 2017: -367, 2018: +0, 2019: +0

## Families after a Holm correction (walk-forward, unfavorable costs)

| family | p | Holm-adjusted p |
|---|---|---|
| event_long | 0.684 | 0.684 |
