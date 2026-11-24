# In-sample study: oanda (2009-11-02 .. 2019-12-31, 2558 sessions)

Every grid point under three cost scenarios; walk-forward selection on the unfavorable scenario.
Proxy data (S&P 500 index CFD scaled to SPY dollars). Not out-of-sample.

## orb

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"minutes": 5, "stop": "far", "target_r": null}` | 2553 | -45,214 | -1.60 | 48.5% | 37% | 0.77 | -0.151 |
| `{"minutes": 5, "stop": "mid", "target_r": null}` | 2553 | -44,580 | -1.63 | 47.0% | 27% | 0.75 | -0.218 |
| `{"minutes": 5, "stop": "far", "target_r": 2.0}` | 2553 | -43,778 | -1.66 | 45.8% | 38% | 0.77 | -0.148 |
| `{"minutes": 5, "stop": "far", "target_r": 1.0}` | 2553 | -44,110 | -2.06 | 45.2% | 47% | 0.75 | -0.157 |
| `{"minutes": 5, "stop": "mid", "target_r": 2.0}` | 2553 | -46,159 | -2.19 | 47.3% | 33% | 0.72 | -0.258 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## gap_fill

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"min_gap": 0.002, "max_gap": 0.01, "entry_minute": 4}` | 1356 | -16,266 | -0.80 | 17.0% | 29% | 0.84 | -0.457 |
| `{"min_gap": 0.002, "max_gap": 0.005, "entry_minute": 4}` | 867 | -12,152 | -0.87 | 12.6% | 30% | 0.79 | -0.537 |
| `{"min_gap": 0.002, "max_gap": 0.005, "entry_minute": 0}` | 874 | -12,039 | -0.97 | 12.8% | 21% | 0.76 | -0.719 |
| `{"min_gap": 0.002, "max_gap": 0.01, "entry_minute": 0}` | 1361 | -18,484 | -1.06 | 19.3% | 20% | 0.77 | -0.377 |
| `{"min_gap": 0.002, "max_gap": 0.01, "entry_minute": 14}` | 1309 | -21,860 | -1.15 | 24.8% | 36% | 0.76 | -0.195 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## sweep

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"levels": ["prev"], "target_r": 2.0}` | 1218 | -20,064 | -1.61 | 21.0% | 35% | 0.70 | -0.409 |
| `{"levels": ["pm"], "target_r": 2.0}` | 1884 | -29,265 | -2.02 | 29.3% | 33% | 0.70 | -0.386 |
| `{"levels": ["prev", "pm"], "target_r": 2.0}` | 2158 | -34,850 | -2.33 | 35.1% | 33% | 0.68 | -0.408 |
| `{"levels": ["prev"], "target_r": 1.0}` | 1212 | -23,298 | -2.56 | 23.7% | 45% | 0.56 | -0.483 |
| `{"levels": ["pm"], "target_r": 1.0}` | 1884 | -29,858 | -2.85 | 29.9% | 47% | 0.61 | -0.373 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## vwap_trend

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"start_minute": 15, "stop_atr": 2.0, "target_r": 2.5}` | 3387 | -49,213 | -1.97 | 50.4% | 36% | 0.77 | -0.175 |
| `{"start_minute": 30, "stop_atr": 2.0, "target_r": 2.5}` | 2564 | -43,250 | -2.03 | 44.1% | 36% | 0.73 | -0.199 |
| `{"start_minute": 30, "stop_atr": 2.0, "target_r": 1.5}` | 2644 | -46,598 | -2.45 | 48.0% | 39% | 0.71 | -0.211 |
| `{"start_minute": 15, "stop_atr": 2.0, "target_r": 1.5}` | 3499 | -53,924 | -2.49 | 55.1% | 39% | 0.74 | -0.192 |
| `{"start_minute": 15, "stop_atr": 1.0, "target_r": 2.5}` | 3856 | -60,652 | -3.11 | 61.4% | 30% | 0.68 | -0.348 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## vwap_reversion

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"stretch_atr": 3.5, "rsi_extreme": 20.0, "stop_atr": 2.0}` | 421 | -5,218 | -0.55 | 5.2% | 37% | 0.81 | -0.175 |
| `{"stretch_atr": 3.5, "rsi_extreme": 20.0, "stop_atr": 1.0}` | 484 | -8,409 | -0.87 | 8.5% | 23% | 0.68 | -0.479 |
| `{"stretch_atr": 2.5, "rsi_extreme": 20.0, "stop_atr": 2.0}` | 535 | -10,157 | -0.95 | 10.2% | 35% | 0.72 | -0.254 |
| `{"stretch_atr": 2.5, "rsi_extreme": 20.0, "stop_atr": 1.0}` | 606 | -12,253 | -1.19 | 12.7% | 22% | 0.64 | -0.664 |
| `{"stretch_atr": 3.5, "rsi_extreme": 30.0, "stop_atr": 2.0}` | 2229 | -32,317 | -1.79 | 32.4% | 38% | 0.74 | -0.194 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

## momentum

Top 5 grid points by full-period Sharpe (normal costs; in-sample, optimistic by construction):

| params | trades | net $ | Sharpe | max DD | win % | PF | exp R |
|---|---|---|---|---|---|---|---|
| `{"start_minute": 15, "min_relvol": 1.5, "stop_atr": 3.0, "target_r": 1.5}` | 1421 | -27,675 | -1.57 | 29.6% | 41% | 0.72 | -0.140 |
| `{"start_minute": 30, "min_relvol": 1.5, "stop_atr": 3.0, "target_r": 1.5}` | 1189 | -26,567 | -1.71 | 27.0% | 40% | 0.68 | -0.154 |
| `{"start_minute": 15, "min_relvol": 1.5, "stop_atr": 3.0, "target_r": 3.0}` | 1421 | -30,942 | -1.73 | 32.4% | 39% | 0.69 | -0.151 |
| `{"start_minute": 30, "min_relvol": 1.5, "stop_atr": 3.0, "target_r": 3.0}` | 1189 | -29,017 | -1.86 | 29.2% | 39% | 0.64 | -0.167 |
| `{"start_minute": 15, "min_relvol": 1.5, "stop_atr": 1.5, "target_r": 3.0}` | 1421 | -32,042 | -1.98 | 34.6% | 28% | 0.65 | -0.312 |

Walk-forward (params chosen each year on earlier years only):

| costs | sessions | trades | net $ | Sharpe | Sharpe 95% CI | p(mean <= 0) |
|---|---|---|---|---|---|---|
| normal | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| unfavorable | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |
| extreme | 2264 | 0 | 0 | 0.00 | [0.00, 0.00] | 1.000 |

By test year (unfavorable): 2011: +0, 2012: +0, 2013: +0, 2014: +0, 2015: +0, 2016: +0, 2017: +0, 2018: +0, 2019: +0

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
| orb | 1.000 | 1.000 |
| gap_fill | 1.000 | 1.000 |
| sweep | 1.000 | 1.000 |
| vwap_trend | 1.000 | 1.000 |
| vwap_reversion | 1.000 | 1.000 |
| momentum | 1.000 | 1.000 |
| open_drive | 1.000 | 1.000 |
