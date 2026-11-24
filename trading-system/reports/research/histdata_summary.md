# In-sample study: histdata (2010-11-15 .. 2018-12-31, 2040 sessions)

Every grid point under three cost scenarios; walk-forward selection on the unfavorable scenario.
Proxy data (S&P 500 index CFD scaled to SPY dollars). Not out-of-sample.

## orb

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"minutes": 30, "stop": "far", "target_r": null}` | 1496 | -34,633 | -2.40 | 34.9% | 41% | 0.64 | -0.152 |
| `{"minutes": 30, "stop": "far", "target_r": 2.0}` | 1496 | -34,680 | -2.45 | 34.9% | 41% | 0.64 | -0.153 |
| `{"minutes": 30, "stop": "far", "target_r": 1.0}` | 1496 | -33,150 | -2.55 | 33.4% | 45% | 0.63 | -0.143 |
| `{"minutes": 30, "stop": "mid", "target_r": null}` | 1496 | -38,807 | -2.64 | 39.1% | 34% | 0.62 | -0.244 |
| `{"minutes": 15, "stop": "mid", "target_r": 2.0}` | 1687 | -40,450 | -2.71 | 40.7% | 34% | 0.64 | -0.301 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 1755 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 1755 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 1755 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0

## gap_fill

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"min_gap": 0.002, "max_gap": 0.005, "entry_minute": 14}` | 568 | -7,762 | -0.75 | 9.6% | 33% | 0.79 | -0.432 |
| `{"min_gap": 0.002, "max_gap": 0.005, "entry_minute": 4}` | 605 | -7,814 | -0.84 | 8.0% | 30% | 0.78 | -0.537 |
| `{"min_gap": 0.002, "max_gap": 0.01, "entry_minute": 14}` | 897 | -13,779 | -0.98 | 16.4% | 30% | 0.77 | -0.404 |
| `{"min_gap": 0.001, "max_gap": 0.005, "entry_minute": 14}` | 851 | -12,117 | -1.09 | 13.5% | 33% | 0.76 | -0.560 |
| `{"min_gap": 0.001, "max_gap": 0.01, "entry_minute": 14}` | 1180 | -17,844 | -1.24 | 19.7% | 31% | 0.76 | -0.503 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 1755 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 1755 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 1755 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0

## sweep

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"levels": ["pm"], "target_r": 2.0}` | 1008 | -16,313 | -1.76 | 18.0% | 35% | 0.69 | -0.339 |
| `{"levels": ["prev"], "target_r": 2.0}` | 700 | -14,405 | -1.78 | 15.3% | 33% | 0.62 | -0.707 |
| `{"levels": ["prev", "pm"], "target_r": 2.0}` | 1269 | -23,920 | -2.41 | 25.6% | 33% | 0.62 | -0.534 |
| `{"levels": ["pm"], "target_r": 1.0}` | 1008 | -17,445 | -2.53 | 18.2% | 49% | 0.58 | -0.358 |
| `{"levels": ["prev"], "target_r": 1.0}` | 698 | -16,230 | -2.72 | 16.3% | 38% | 0.47 | -0.686 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 1755 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 1755 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 1755 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0

## Families after a Holm correction (walk-forward, unfavorable costs)

| family | p | Holm-adjusted p |
|---|---|---|
| orb | 1.000 | 1.000 |
| gap_fill | 1.000 | 1.000 |
| sweep | 1.000 | 1.000 |
