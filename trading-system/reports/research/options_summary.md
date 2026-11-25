# In-sample options study (MODELED option prices)

2009-11-02 .. 2019-12-31, 2558 sessions. Black-Scholes on VIX x 1.15 with skew, the historical
SPY expiry calendar, spreads scaled to premium, $0.69 a contract a side. Underlying parameters were chosen
in-sample on the whole period, so these figures are optimistic. Not verified options data.

## first_candle {"candle_min": 5, "target_r": 3.0}

| options | trades | net $ | Sharpe | win % | no-cost $ |
|---|---|---|---|---|---|
| `{"delta": 0.5, "min_dte": 0, "premium_stop": 0.5}` | 2351 | -24,779 | -1.80 | 29% | -589 |
| `{"delta": 0.5, "min_dte": 0, "premium_stop": null}` | 2294 | -10,992 | -1.82 | 29% | -425 |
| `{"delta": 0.5, "min_dte": 1, "premium_stop": 0.5}` | 2343 | -16,719 | -1.83 | 30% | 1,403 |

Walk-forward: normal $0 (Sharpe 0.00, 0 trades), unfavorable $0 (Sharpe 0.00, 0 trades), extreme $0 (Sharpe 0.00, 0 trades)

## vol_breakout {"k": 0.35}

| options | trades | net $ | Sharpe | win % | no-cost $ |
|---|---|---|---|---|---|
| `{"delta": 0.5, "min_dte": 1, "premium_stop": null}` | 1433 | -3,910 | -1.06 | 40% | 1,061 |
| `{"delta": 0.5, "min_dte": 1, "premium_stop": 0.5}` | 1507 | -10,858 | -1.28 | 40% | 1,035 |
| `{"delta": 0.5, "min_dte": 0, "premium_stop": null}` | 1478 | -8,577 | -1.56 | 39% | -1,568 |

Walk-forward: normal $0 (Sharpe 0.00, 0 trades), unfavorable $0 (Sharpe 0.00, 0 trades), extreme $0 (Sharpe 0.00, 0 trades)

## gap_go {"min_gap": 0.003, "candle_min": 5}

| options | trades | net $ | Sharpe | win % | no-cost $ |
|---|---|---|---|---|---|
| `{"delta": 0.5, "min_dte": 1, "premium_stop": null}` | 493 | -473 | -0.21 | 29% | 1,189 |
| `{"delta": 0.5, "min_dte": 1, "premium_stop": 0.5}` | 541 | -1,572 | -0.30 | 29% | 2,444 |
| `{"delta": 0.5, "min_dte": 0, "premium_stop": null}` | 519 | -1,593 | -0.50 | 28% | 594 |

Walk-forward: normal $-317 (Sharpe -0.36, 66 trades), unfavorable $-439 (Sharpe -0.49, 66 trades), extreme $-749 (Sharpe -0.82, 66 trades)

## gap_go {"min_gap": 0.005, "candle_min": 5}

| options | trades | net $ | Sharpe | win % | no-cost $ |
|---|---|---|---|---|---|
| `{"delta": 0.5, "min_dte": 1, "premium_stop": null}` | 266 | 454 | 0.25 | 35% | 1,342 |
| `{"delta": 0.5, "min_dte": 1, "premium_stop": 0.5}` | 304 | 517 | 0.13 | 33% | 2,673 |
| `{"delta": 0.5, "min_dte": 0, "premium_stop": null}` | 284 | 55 | 0.02 | 33% | 1,176 |

Walk-forward: normal $-83 (Sharpe -0.06, 201 trades), unfavorable $-399 (Sharpe -0.29, 201 trades), extreme $-1,208 (Sharpe -0.88, 201 trades)

## oops {"min_gap": 0.0, "target_r": null}

| options | trades | net $ | Sharpe | win % | no-cost $ |
|---|---|---|---|---|---|
| `{"delta": 0.5, "min_dte": 1, "premium_stop": 0.5}` | 500 | -2,296 | -0.44 | 34% | 1,641 |
| `{"delta": 0.5, "min_dte": 1, "premium_stop": null}` | 468 | -1,047 | -0.46 | 34% | 591 |
| `{"delta": 0.5, "min_dte": 0, "premium_stop": 0.5}` | 503 | -6,188 | -0.81 | 33% | -656 |

Walk-forward: normal $0 (Sharpe 0.00, 0 trades), unfavorable $0 (Sharpe 0.00, 0 trades), extreme $0 (Sharpe 0.00, 0 trades)

## nr_orb {"nr": 4, "minutes": 5, "stop": "far", "target_r": null}

| options | trades | net $ | Sharpe | win % | no-cost $ |
|---|---|---|---|---|---|
| `{"delta": 0.5, "min_dte": 1, "premium_stop": null}` | 635 | -2,125 | -0.78 | 34% | 32 |
| `{"delta": 0.5, "min_dte": 1, "premium_stop": 0.5}` | 667 | -5,271 | -0.86 | 34% | -74 |
| `{"delta": 0.5, "min_dte": 0, "premium_stop": 0.5}` | 670 | -9,255 | -1.04 | 33% | -2,458 |

Walk-forward: normal $0 (Sharpe 0.00, 0 trades), unfavorable $0 (Sharpe 0.00, 0 trades), extreme $0 (Sharpe 0.00, 0 trades)

## gap_fill {"min_gap": 0.002, "max_gap": 0.01, "entry_minute": 4}

| options | trades | net $ | Sharpe | win % | no-cost $ |
|---|---|---|---|---|---|
| `{"delta": 0.5, "min_dte": 0, "premium_stop": null}` | 1282 | -5,802 | -1.56 | 27% | 15 |
| `{"delta": 0.5, "min_dte": 0, "premium_stop": 0.5}` | 1317 | -13,021 | -1.56 | 27% | 331 |
| `{"delta": 0.5, "min_dte": 1, "premium_stop": null}` | 1230 | -3,858 | -1.58 | 27% | 263 |

Walk-forward: normal $0 (Sharpe 0.00, 0 trades), unfavorable $0 (Sharpe 0.00, 0 trades), extreme $0 (Sharpe 0.00, 0 trades)

## vwap_reversion {"stretch_atr": 3.5, "rsi_extreme": 20.0, "stop_atr": 2.0}

| options | trades | net $ | Sharpe | win % | no-cost $ |
|---|---|---|---|---|---|
| `{"delta": 0.5, "min_dte": 0, "premium_stop": 0.5}` | 415 | -3,505 | -0.81 | 35% | 959 |
| `{"delta": 0.5, "min_dte": 0, "premium_stop": null}` | 412 | -1,755 | -0.89 | 34% | 214 |
| `{"delta": 0.5, "min_dte": 1, "premium_stop": 0.5}` | 414 | -3,079 | -1.05 | 36% | 295 |

Walk-forward: normal $0 (Sharpe 0.00, 0 trades), unfavorable $0 (Sharpe 0.00, 0 trades), extreme $0 (Sharpe 0.00, 0 trades)

## orb {"minutes": 5, "stop": "far", "target_r": null}

| options | trades | net $ | Sharpe | win % | no-cost $ |
|---|---|---|---|---|---|
| `{"delta": 0.3, "min_dte": 0, "premium_stop": 0.5}` | 2484 | -94,002 | -1.75 | 28% | -36,936 |
| `{"delta": 0.5, "min_dte": 1, "premium_stop": null}` | 2337 | -11,392 | -2.26 | 33% | -3,397 |
| `{"delta": 0.5, "min_dte": 1, "premium_stop": 0.5}` | 2476 | -26,624 | -2.26 | 33% | -7,411 |

Walk-forward: normal $0 (Sharpe 0.00, 0 trades), unfavorable $0 (Sharpe 0.00, 0 trades), extreme $0 (Sharpe 0.00, 0 trades)

## noise_momentum {"mult": 1.5, "every": 30}

| options | trades | net $ | Sharpe | win % | no-cost $ |
|---|---|---|---|---|---|
| `{"delta": 0.5, "min_dte": 1, "premium_stop": null}` | 474 | -803 | -0.38 | 40% | 879 |
| `{"delta": 0.5, "min_dte": 1, "premium_stop": 0.5}` | 501 | -2,139 | -0.45 | 40% | 1,863 |
| `{"delta": 0.5, "min_dte": 0, "premium_stop": null}` | 492 | -1,916 | -0.63 | 39% | 389 |

Walk-forward: normal $0 (Sharpe 0.00, 0 trades), unfavorable $0 (Sharpe 0.00, 0 trades), extreme $0 (Sharpe 0.00, 0 trades)

## Holm correction across the options families (walk-forward, unfavorable)

| family | p | Holm p |
|---|---|---|
| opt_first_candle_{"candle_min": 5, "target_r": 3.0} | 1.000 | 1.000 |
| opt_vol_breakout_{"k": 0.35} | 1.000 | 1.000 |
| opt_gap_go_{"candle_min": 5, "min_gap": 0.003} | 0.920 | 1.000 |
| opt_gap_go_{"candle_min": 5, "min_gap": 0.005} | 0.840 | 1.000 |
| opt_oops_{"min_gap": 0.0, "target_r": null} | 1.000 | 1.000 |
| opt_nr_orb_{"minutes": 5, "nr": 4, "stop": "far", "target_r": null} | 1.000 | 1.000 |
| opt_gap_fill_{"entry_minute": 4, "max_gap": 0.01, "min_gap": 0.002} | 1.000 | 1.000 |
| opt_vwap_reversion_{"rsi_extreme": 20.0, "stop_atr": 2.0, "stretch_atr": 3.5} | 1.000 | 1.000 |
| opt_orb_{"minutes": 5, "stop": "far", "target_r": null} | 1.000 | 1.000 |
| opt_noise_momentum_{"every": 30, "mult": 1.5} | 1.000 | 1.000 |
