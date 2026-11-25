# PROJECT_STATUS

Last updated: 2026-11-24 (evening, New York). Read this first when continuing in a new session.

## Headline

**No edge found yet.** On 2010-2019 S&P 500 minute data (free proxies scaled to SPY dollars),
none of the seven strategy families makes money after realistic costs. Walk-forward selection
chose "no trade" in every family and every year. Nothing has been frozen; the 2020-2026
holdout is untouched. A live PAPER engine runs all families in shadow mode on real SPY bars.

## Data (no Databento account; free sources, see docs/DATA_DECISION.md, revision 3)

| Role | Source | Span | Status |
|---|---|---|---|
| In-sample | Oanda SPX500_USD 1-min (public GitHub mirror) | 2010-01..2019-12 | loaded, quality report in study output |
| Replication | HistData SPXUSD 1-min | 2010-11..2018-12 | loaded, reports/data_quality_histdata.md |
| Holdout | HistData SPXUSD 1-min (histdata.com) | 2020-01..2026-11-13 | downloaded, NOT looked at |
| Second holdout | Dukascopy USA500.IDX | 2020+ | downloading, throttled (429) |
| Real SPY | Yahoo 1-min (last ~30 days), daily since 1993 | | loaded |

Proxy check (reports/proxy_vs_spy.md): 1-minute return correlation with real SPY 0.988, same
15-minute range-break direction on 22 of 22 days.

## In-sample results (reports/research/)

- 7 families plus 2 limit-entry variants, 132 parameter combinations, each under three cost scenarios (trial_log.jsonl).
- Before costs a few combinations are slightly positive (gap fill about +$8 a trade on both
  vendors, VWAP reversion, liquidity sweep); after normal costs (~$20-25 a trade at 1x leverage) every one loses.
- ORB loses even before costs (-$6k to -$19k over the decade on $100k).
- Time of day (time_of_day.md): 5-minute returns are positively autocorrelated only in
  9:30-9:45 (+0.084), slightly mean-reverting after; 10:45-11:00 drifts -0.85 bp a day
  (t -3.2). Both are below SPY's round-trip cost. The open-drive family built on the first
  finding was declared before testing and also failed walk-forward.
- Cheaper execution (limit entries at the signal close, filled only on a one-tick trade-through,
  no spread or slippage) for gap fill and VWAP reversion: gap fill gets worse (adverse
  selection: the limits fill when price runs against the fade), VWAP reversion improves only
  from -$5.2k to -$4.5k. Both still no trade in walk-forward. (oanda_gap_fill_limit-vwap_reversion_limit_summary.md)

- Published / famous-trader rules (adapted to 9:30-11:00): Zarattini-Aziz first-candle ORB,
  Zarattini-Aziz-Barbon noise-area momentum, Crabel NR4/NR7 ORB, Williams volatility breakout
  and Oops, Raschke Turtle Soup, calendar effects (turn of month, pre-holiday, Monday, Friday),
  gap-and-go, VIX-regime and daily-trend filters, a walk-forward ML direction model
  (logit / LightGBM on 20 features), and the pre-FOMC drift (Lucca-Moench; dates from
  federalreserve.gov). None passes walk-forward; best in-sample Sharpe about 0.36 (gap-and-go,
  gaps >= 0.5%). The ML model's out-of-sample predictions win 44-48%.
- Pre-cost edges of the best grid points are 1.5-5.2 cents a share per trade (t 1.4-2.1, before
  any multiple-testing correction) against a modelled 3.7c round trip; a 1c round trip (spread
  only, $0 commission) would leave a few marginally positive but statistically weak.
- Informational only (outside the window, reports/research/fullday_informational.json): holding
  to the close does not rescue them on 2010-2019 data (noise momentum Sharpe -0.43..+0.15).
- About 30 families and 215 parameter sets have been tested in total (trial_log.jsonl).

## Options (user direction 2026-11-25: trade SPY options in 9:30-11:00)

- No free historical SPY option quotes exist here (no Databento account; Cboe's delayed chain
  refuses automated access; Yahoo's chain needs a login cookie). The options engine is therefore
  MODELED: Black-Scholes in trading-variance time, VIX x 1.15 with skew (0DTE ATM straddle at
  9:35 ~0.73% of spot, near market levels), the historical expiry calendar (daily 0DTE only from
  Nov 2022), spreads scaled to premium, $0.69 a contract a side, premium stops/targets.
- Long calls/puts on the ten best underlying signals, 8 contract settings each, three cost
  scenarios (reports/research/options_summary.md): every family loses; walk-forward picks no
  trade; best is gap-and-go (gaps >= 0.5%) with 1-DTE ATM options, +$454 over 10 years.
- Why: from 9:35 to 10:55 SPY moved on average 72% of what the option model implies (83% at
  VIX x 1.0). Option buyers overpay for movement (the volatility risk premium), so buying
  options makes weak directional signals worse.
- The structurally positive side is selling premium with defined risk (credit spreads / iron
  condors), but a modeled test of that would only echo the assumed implied volatility: it needs
  real option quotes (paid history, or recording real chains going forward).
- The live paper engine records each shadow trade as a modeled ATM option with a 50% premium
  stop (one contract at SPY ~$720 risks more than the $250 budget at full premium).

## Running (restart at each 2-hour limit)

- SPY live paper engine: `python scripts/paper_live.py --minutes 115` (sleeps outside 9:30-11:00;
  records only sessions it watched live; reports/paper/, state/signals.db).
- Dukascopy holdout download: `python scripts/fetch_dukascopy.py 2020-01-01 2026-10-25` (resumable).
- The Kalshi bots (separate project in the repo root).

## Built (src/tradesys/)

Calendar and 9:30-11:00 window; Databento client with two-key spend guard (unused: no account);
free sources (Oanda, HistData, Dukascopy, Yahoo) and SPY-dollar proxy scaling; validation and
data-quality reports; causal leak-tested features; 7 strategy families; event-driven backtester
(next-open fills, gap stops, stop-first, trade-through limits, volume cap, three cost
scenarios, session cache); risk manager and persistent kill switch; metrics with block
bootstrap; research runner (in-sample guard, trial log, walk-forward, Holm); freeze and
holdout guard; signal store; live paper engine; limit-order entries. 26 tests pass.

## Not built yet

Options engine (no free historical options data with real bid/ask), regime classifier,
dashboard, broker paper integration, Monte Carlo report. These wait for either a validated
strategy or the data they need.

## Next steps

1. Keep collecting forward paper sessions on real SPY (shadow; tells whether live matches backtest).
2. Research ideas not yet tested, each pre-declared in `research.GRIDS` before running:
   longer holds (to 11:00) with wider stops, filters by VIX / gap size / day of week,
   cost-reducing execution (limit entries).
3. If a family ever passes in-sample (walk-forward positive under unfavorable costs, Holm p < 0.05),
   freeze it (`main.py freeze`) and run the HistData holdout once.
4. Options research needs data with real quotes (OPRA via a paid vendor): ask the user before any cost.
