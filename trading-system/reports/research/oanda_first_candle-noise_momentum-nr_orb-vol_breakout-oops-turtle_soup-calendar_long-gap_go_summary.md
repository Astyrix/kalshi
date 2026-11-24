# In-sample study: oanda (2009-11-02 .. 2019-12-31, 2558 sessions)

Every grid point under three cost scenarios; walk-forward selection on the unfavorable scenario.
Proxy data (S&P 500 index CFD scaled to SPY dollars). Not out-of-sample.

## first_candle

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"candle_min": 5, "target_r": 3.0}` | 2416 | -28,403 | -1.00 | 30.9% | 32% | 0.84 | -0.191 |
| `{"candle_min": 30, "target_r": 3.0}` | 2463 | -27,630 | -1.08 | 30.8% | 43% | 0.84 | -0.083 |
| `{"candle_min": 5, "target_r": null}` | 2416 | -32,763 | -1.09 | 36.6% | 29% | 0.82 | -0.145 |
| `{"candle_min": 30, "target_r": null}` | 2463 | -30,900 | -1.25 | 33.5% | 43% | 0.81 | -0.087 |
| `{"candle_min": 15, "target_r": 3.0}` | 2452 | -45,363 | -1.84 | 47.3% | 38% | 0.74 | -0.179 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## noise_momentum

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"mult": 1.5, "every": 30}` | 520 | -1,052 | -0.23 | 2.0% | 46% | 0.92 | -0.009 |
| `{"mult": 1.5, "every": 15}` | 694 | -1,782 | -0.40 | 2.5% | 41% | 0.87 | -0.011 |
| `{"mult": 1.0, "every": 30}` | 870 | -7,165 | -1.00 | 7.8% | 43% | 0.74 | -0.036 |
| `{"mult": 1.5, "every": 5}` | 1035 | -5,049 | -1.11 | 5.6% | 32% | 0.72 | -0.021 |
| `{"mult": 1.0, "every": 15}` | 1157 | -8,884 | -1.28 | 9.4% | 37% | 0.72 | -0.034 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## nr_orb

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"nr": 7, "minutes": 5, "stop": "far", "target_r": null}` | 393 | -1,401 | -0.09 | 7.1% | 39% | 0.96 | -0.039 |
| `{"nr": 4, "minutes": 5, "stop": "far", "target_r": null}` | 684 | -6,535 | -0.33 | 13.0% | 38% | 0.90 | -0.064 |
| `{"nr": 7, "minutes": 15, "stop": "far", "target_r": null}` | 386 | -7,493 | -0.67 | 9.6% | 41% | 0.76 | -0.105 |
| `{"nr": 4, "minutes": 15, "stop": "far", "target_r": null}` | 670 | -12,255 | -0.86 | 14.0% | 42% | 0.77 | -0.097 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## vol_breakout

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"k": 0.5}` | 957 | -6,973 | -0.48 | 10.4% | 45% | 0.87 | -0.028 |
| `{"k": 0.35}` | 1555 | -11,535 | -0.54 | 16.3% | 45% | 0.89 | -0.035 |
| `{"k": 0.2}` | 2292 | -25,066 | -0.85 | 31.5% | 39% | 0.86 | -0.059 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## oops

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"min_gap": 0.0, "target_r": null}` | 517 | -665 | -0.04 | 5.2% | 38% | 0.98 | -0.050 |
| `{"min_gap": 0.001, "target_r": null}` | 285 | -456 | -0.04 | 2.6% | 42% | 0.98 | -0.005 |
| `{"min_gap": 0.001, "target_r": 2.0}` | 285 | -917 | -0.09 | 2.5% | 42% | 0.95 | -0.015 |
| `{"min_gap": 0.0, "target_r": 2.0}` | 517 | -2,803 | -0.22 | 4.2% | 40% | 0.92 | -0.100 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## turtle_soup

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"target_r": null}` | 390 | -7,783 | -0.78 | 9.1% | 28% | 0.71 | -0.157 |
| `{"target_r": 2.0}` | 390 | -7,184 | -0.85 | 8.2% | 36% | 0.70 | -0.304 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## calendar_long

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"days": "pre_holiday"}` | 94 | 97 | 0.05 | 0.5% | 53% | 1.04 | +0.004 |
| `{"days": "monday"}` | 478 | -1,231 | -0.20 | 2.3% | 49% | 0.92 | -0.010 |
| `{"days": "tom"}` | 485 | -3,783 | -0.61 | 5.2% | 50% | 0.80 | -0.032 |
| `{"days": "friday"}` | 511 | -3,684 | -0.64 | 4.2% | 47% | 0.78 | -0.030 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 144 | -953 | -0.27 | [-0.76, 0.24] | 0.840 |
| unfavorable | 2264 | 144 | -1,563 | -0.45 | [-0.94, 0.05] | 0.952 |
| extreme | 2264 | 144 | -3,168 | -0.97 | [-1.43, -0.46] | 1.000 |

By test year (unfavorable): 2011: -178, 2012: -746, 2013: +0, 2014: -639, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## gap_go

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"min_gap": 0.005, "candle_min": 15}` | 317 | 4,885 | 0.36 | 3.6% | 48% | 1.17 | +0.079 |
| `{"min_gap": 0.005, "candle_min": 5}` | 326 | 4,919 | 0.30 | 2.8% | 35% | 1.14 | +0.147 |
| `{"min_gap": 0.003, "candle_min": 5}` | 564 | -1,198 | -0.05 | 5.5% | 31% | 0.98 | -0.055 |
| `{"min_gap": 0.003, "candle_min": 15}` | 551 | -2,019 | -0.11 | 8.0% | 42% | 0.96 | -0.063 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 180 | -264 | -0.02 | [-0.66, 0.56] | 0.534 |
| unfavorable | 2264 | 180 | -3,374 | -0.32 | [-0.99, 0.29] | 0.850 |
| extreme | 2264 | 180 | -10,301 | -1.14 | [-1.83, -0.45] | 1.000 |

By test year (unfavorable): 2011: +136, 2012: -1,717, 2013: +0, 2014: -785, 2015: +0, 2016: +1,611, 2017: -535, 2018: -2,084, 2019: +0

## Families after a Holm correction (walk-forward, unfavorable costs)

| family | p | Holm-adjusted p |
|---|---|---|
| first_candle | 1.000 | 1.000 |
| noise_momentum | 1.000 | 1.000 |
| nr_orb | 1.000 | 1.000 |
| vol_breakout | 1.000 | 1.000 |
| oops | 1.000 | 1.000 |
| turtle_soup | 1.000 | 1.000 |
| calendar_long | 0.952 | 1.000 |
| gap_go | 0.850 | 1.000 |
