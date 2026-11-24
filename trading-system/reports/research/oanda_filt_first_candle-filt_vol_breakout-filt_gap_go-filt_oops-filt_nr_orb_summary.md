# In-sample study: oanda (2009-11-02 .. 2019-12-31, 2558 sessions)

Every grid point under three cost scenarios; walk-forward selection on the unfavorable scenario.
Proxy data (S&P 500 index CFD scaled to SPY dollars). Not out-of-sample.

## filt_first_candle

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"base": "first_candle", "base_params": {"candle_min": 5, "target_r": 3.0}, "vix": "high", "trend": "with"}` | 222 | -7,432 | -0.58 | 7.4% | 27% | 0.74 | -0.265 |
| `{"base": "first_candle", "base_params": {"candle_min": 5, "target_r": 3.0}, "vix": "low", "trend": "with"}` | 875 | -9,869 | -0.59 | 14.0% | 33% | 0.85 | -0.197 |
| `{"base": "first_candle", "base_params": {"candle_min": 5, "target_r": 3.0}, "vix": "high", "trend": "any"}` | 530 | -12,705 | -0.66 | 12.7% | 30% | 0.80 | -0.205 |
| `{"base": "first_candle", "base_params": {"candle_min": 5, "target_r": 3.0}, "vix": "low", "trend": "any"}` | 1886 | -17,978 | -0.76 | 22.3% | 33% | 0.87 | -0.187 |
| `{"base": "first_candle", "base_params": {"candle_min": 5, "target_r": 3.0}, "vix": "any", "trend": "with"}` | 1097 | -16,573 | -0.82 | 18.4% | 32% | 0.82 | -0.211 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## filt_vol_breakout

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"base": "vol_breakout", "base_params": {"k": 0.35}, "vix": "low", "trend": "with"}` | 572 | -2,477 | -0.19 | 5.6% | 45% | 0.93 | -0.016 |
| `{"base": "vol_breakout", "base_params": {"k": 0.35}, "vix": "high", "trend": "any"}` | 310 | -2,547 | -0.25 | 4.8% | 46% | 0.89 | -0.031 |
| `{"base": "vol_breakout", "base_params": {"k": 0.35}, "vix": "any", "trend": "with"}` | 714 | -6,005 | -0.42 | 8.0% | 45% | 0.87 | -0.035 |
| `{"base": "vol_breakout", "base_params": {"k": 0.35}, "vix": "low", "trend": "any"}` | 1245 | -9,222 | -0.48 | 12.8% | 44% | 0.89 | -0.035 |
| `{"base": "vol_breakout", "base_params": {"k": 0.35}, "vix": "any", "trend": "any"}` | 1555 | -11,535 | -0.54 | 16.3% | 45% | 0.89 | -0.035 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## filt_gap_go

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"base": "gap_go", "base_params": {"min_gap": 0.003, "candle_min": 5}, "vix": "any", "trend": "with"}` | 239 | 2,925 | 0.22 | 3.0% | 33% | 1.13 | +0.150 |
| `{"base": "gap_go", "base_params": {"min_gap": 0.003, "candle_min": 5}, "vix": "low", "trend": "with"}` | 171 | 1,980 | 0.21 | 4.6% | 36% | 1.14 | +0.134 |
| `{"base": "gap_go", "base_params": {"min_gap": 0.003, "candle_min": 5}, "vix": "high", "trend": "with"}` | 68 | 928 | 0.10 | 1.7% | 28% | 1.11 | +0.192 |
| `{"base": "gap_go", "base_params": {"min_gap": 0.003, "candle_min": 5}, "vix": "high", "trend": "any"}` | 178 | 345 | 0.03 | 3.4% | 31% | 1.02 | +0.070 |
| `{"base": "gap_go", "base_params": {"min_gap": 0.003, "candle_min": 5}, "vix": "any", "trend": "any"}` | 564 | -1,198 | -0.05 | 5.5% | 31% | 0.98 | -0.055 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 29 | -2,436 | -0.79 | [-1.56, -0.02] | 0.974 |
| unfavorable | 2264 | 29 | -2,886 | -0.92 | [-1.65, -0.15] | 0.986 |
| extreme | 2264 | 29 | -3,877 | -1.20 | [-1.85, -0.50] | 0.994 |

By test year (unfavorable): 2011: +0, 2012: -981, 2013: +0, 2014: -1,905, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## filt_oops

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"base": "oops", "base_params": {"min_gap": 0.0, "target_r": null}, "vix": "any", "trend": "with"}` | 195 | 686 | 0.08 | 2.3% | 38% | 1.05 | +0.033 |
| `{"base": "oops", "base_params": {"min_gap": 0.0, "target_r": null}, "vix": "low", "trend": "with"}` | 154 | 478 | 0.07 | 2.0% | 37% | 1.05 | +0.040 |
| `{"base": "oops", "base_params": {"min_gap": 0.0, "target_r": null}, "vix": "low", "trend": "any"}` | 414 | 694 | 0.06 | 3.9% | 36% | 1.02 | -0.043 |
| `{"base": "oops", "base_params": {"min_gap": 0.0, "target_r": null}, "vix": "high", "trend": "with"}` | 41 | 207 | 0.04 | 1.1% | 44% | 1.05 | +0.007 |
| `{"base": "oops", "base_params": {"min_gap": 0.0, "target_r": null}, "vix": "any", "trend": "any"}` | 517 | -665 | -0.04 | 5.2% | 38% | 0.98 | -0.050 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 33 | -2,344 | -1.03 | [-1.44, -0.46] | 1.000 |
| unfavorable | 2264 | 33 | -2,719 | -1.16 | [-1.54, -0.61] | 1.000 |
| extreme | 2264 | 33 | -3,614 | -1.41 | [-1.74, -0.89] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: -1,830, 2013: +0, 2014: +0, 2015: +0, 2016: -547, 2017: +0, 2018: -342, 2019: +0

## filt_nr_orb

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"base": "nr_orb", "base_params": {"nr": 4, "minutes": 5, "stop": "far", "target_r": null}, "vix": "high", "trend": "any"}` | 139 | 686 | 0.06 | 3.7% | 40% | 1.04 | -0.006 |
| `{"base": "nr_orb", "base_params": {"nr": 4, "minutes": 5, "stop": "far", "target_r": null}, "vix": "any", "trend": "any"}` | 684 | -6,535 | -0.33 | 13.0% | 38% | 0.90 | -0.064 |
| `{"base": "nr_orb", "base_params": {"nr": 4, "minutes": 5, "stop": "far", "target_r": null}, "vix": "low", "trend": "with"}` | 266 | -3,425 | -0.34 | 5.5% | 40% | 0.85 | -0.059 |
| `{"base": "nr_orb", "base_params": {"nr": 4, "minutes": 5, "stop": "far", "target_r": null}, "vix": "low", "trend": "any"}` | 545 | -7,173 | -0.46 | 11.1% | 38% | 0.85 | -0.079 |
| `{"base": "nr_orb", "base_params": {"nr": 4, "minutes": 5, "stop": "far", "target_r": null}, "vix": "high", "trend": "with"}` | 68 | -3,921 | -0.52 | 5.0% | 31% | 0.61 | -0.321 |

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
| filt_first_candle | 1.000 | 1.000 |
| filt_vol_breakout | 1.000 | 1.000 |
| filt_gap_go | 0.986 | 1.000 |
| filt_oops | 1.000 | 1.000 |
| filt_nr_orb | 1.000 | 1.000 |
