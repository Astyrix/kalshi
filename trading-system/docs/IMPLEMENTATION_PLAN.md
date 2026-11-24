# Implementation plan

Goal: find out whether a repeatable, executable edge exists in SPY between 9:30 and 11:00
New York time, after realistic costs and statistical uncertainty, and build the system
around it only if it does. "No edge found" is an acceptable, reportable outcome.

## Principles

1. **Data before strategies.** No strategy result before a data-quality report exists for the data it ran on.
2. **One look at the holdout.** 2010-2019 for discovery (chronological walk-forward inside it);
   2020 onward run once, on a frozen spec (`main.py freeze`), and every run is logged
   (`config/frozen/oos_runs.jsonl`). A second look marks the holdout contaminated.
3. **Pessimistic execution.** Signal at a bar's close, fill at the next open plus half spread
   and slippage; stop first when a bar touches both; limits need a trade-through; volume caps;
   three cost scenarios. A strategy that only works under "normal" costs is rejected.
4. **Count every test.** Each strategy/parameter combination tried is logged; significance is
   judged with a multiple-testing correction (White's Reality Check / Hansen SPA, or
   Benjamini-Hochberg across the family) and block-bootstrap confidence intervals.
5. **Risk before return.** Sizing by stop distance (0.25% of equity), daily/weekly loss
   limits, losing-streak halt, one position, five trades a session, persistent kill switch.

## Phases

| Phase | Work | Needs | State |
|---|---|---|---|
| 1 | Repository and environment inspection, skill audit and install, project scaffold | - | done |
| 2 | Data availability and cost: Databento metadata (ranges, schemas, `get_cost`), alternatives for 2010-2018 | working Databento key | blocked on key (401) |
| 3 | Ingestion: guarded incremental downloads to Parquet, validation, data-quality report | Phase 2 + spend approval | code done, untested against the live API |
| 4 | Features (causal, leak-tested) and baseline strategies (ORB, VWAP trend/reversion, gap fill, momentum, liquidity sweep) | - | done (tested on synthetic bars only) |
| 5 | Event-driven backtester, cost model, risk engine, kill switch, metrics, signal store | - | done (tested) |
| 6 | In-sample research 2010-2019: per-strategy walk-forward (e.g. 3-year train / 1-year test, rolling), parameter-sensitivity surfaces, time-of-day analysis of the first 90 minutes, regime breakdowns; trial log | data | not started |
| 7 | Freeze the survivors; single out-of-sample run 2020-2026; report both periods side by side | Phase 6 | freeze/OOS guard built |
| 8 | Options: OPRA definitions for real expirations/strikes, CBBO-1m for real bid/ask, contract selection (delta/DTE), premium stops; verified vs modeled results kept apart | OPRA data (2013+) | not started |
| 9 | Stress tests: cost multipliers, delays (latency_bars), missing data, block-bootstrap and Monte Carlo of trade sequences, risk of ruin, recovery times | Phase 6-7 | metrics/bootstrap built |
| 10 | Real-time signal engine and Streamlit dashboard (clearly labelled BACKTEST / REPLAY / PAPER / LIVE), manual emergency stop | live data feed | not started |
| 11 | Paper trading through a broker-independent interface; broker evaluation (Alpaca, Interactive Brokers, Tradier, Schwab: to verify API, paper accounts, options support, costs, NY availability) | broker paper account | not started |
| 12 | Final audit and documentation | all | ongoing |

## Research protocol for Phase 6 (fixed before seeing any result)

- Unit of evidence: daily net P&L over all sessions (zeros included), not per-trade win rate.
- A strategy passes in-sample only if, in walk-forward test folds: mean daily return > 0 with
  bootstrap p < 0.05 after the family-wise correction; positive under the "unfavorable" cost
  scenario; positive in at least 60% of years; no single year contributing over 40% of profit;
  parameter neighbourhood (plus/minus one grid step) also positive.
- Benchmarks reported alongside: cash (0), SPY buy-and-hold (daily), SPY 9:30-11:00 long every
  day, a plain 15-minute ORB.
- Time-of-day study: the same rules with entry windows 9:31-9:45, 9:45-10:15, 10:15-10:50.

## Not done until data exists

No performance number in this repository may be produced from synthetic data. The synthetic
bars in `tests/` test mechanics (timing, fills, leakage) only.
