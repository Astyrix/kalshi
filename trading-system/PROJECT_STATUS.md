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
