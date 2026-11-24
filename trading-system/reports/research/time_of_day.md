# The first 90 minutes: model-free structure (in-sample)

Oanda SPX500 CFD scaled to SPY, 2010-01-04 .. 2019-12-31, 2515 sessions.

| bucket | mean abs 15-min return (bp) | mean return (bp) | t | lag-1 autocorr of 5-min returns |
|---|---|---|---|---|
| 9:30-9:45 | 14.8 | +0.13 | +0.32 | +0.084 |
| 9:45-10:00 | 11.8 | -0.03 | -0.09 | -0.008 |
| 10:00-10:15 | 12.3 | -0.27 | -0.77 | -0.003 |
| 10:15-10:30 | 10.5 | +0.24 | +0.79 | -0.031 |
| 10:30-10:45 | 10.5 | +0.37 | +1.26 | -0.046 |
| 10:45-11:00 | 9.6 | -0.85 | -3.22 | -0.038 |

## Predictive regressions across days (bp; Newey-West t)

| y | x | slope | t | R^2 |
|---|---|---|---|---|
| r1 | gap | -0.0256 | -1.52 | 0.0032 |
| r2 | gap | +0.0001 | +0.00 | 0.0000 |
| r2 | r1 | +0.0001 | +0.00 | 0.0000 |

r1 = 9:30-10:00 return, r2 = 10:00-11:00 return, gap = dividend-adjusted overnight gap.
A slope with |t| < 2 is indistinguishable from zero; even |t| > 2 must survive costs and the holdout.
