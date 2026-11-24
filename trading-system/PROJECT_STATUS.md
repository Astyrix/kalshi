# PROJECT_STATUS

Last updated: 2026-11-24. Read this first when continuing in a new session.

## Where things stand

**No strategy has been tested on real market data yet.** The data source (Databento) is
blocked: the API key saved in the Claude environment for `hist.databento.com` is rejected
with HTTP 401 `auth_authentication_failed`. Everything that does not need data is built and
tested (24 tests, synthetic bars only for mechanics).

## Done

- Phase 1. `Jakeschincariol/day-trader-skill` (commit 3d0f4ef) cloned, audited
  (docs/SKILL_AUDIT.md), its 315 tests pass, installed project-locally in `.claude/skills/`.
  Key findings: 2026-2027-only holiday calendar, entries at the signal bar's close, no costs,
  i.i.d. bar resampling in the "Time Machine". Reused: approval flow, stop-first rule, risk checklist.
- Phase 2 (partial). Coverage research in docs/DATA_SOURCES.md: Databento US equities start
  2018-05-01; OPRA from 2013-04-01 (trades, bars, statistics, definitions, CBBO-1m), CMBP-1
  from 2023-03-28. So 2010 to April 2018 SPY needs another vendor (candidates listed, none bought).
- Phases 3-5 (code). `src/tradesys/`:
  - `config.py` + `config/default.yaml`: typed config, fingerprint for freezing.
  - `market_calendar.py`: XNYS sessions via exchange_calendars, zoneinfo New York, the
    9:30 / 9:31 first fill / 10:50 cutoff / 10:55 flatten / 11:00 hard-flat window.
  - `data_ingestion/`: Databento coverage and cost quotes, guarded month-by-month downloads
    (two-key spend guard), atomic Parquet store with manifest, validation + markdown report.
  - `feature_engineering/features.py`: daily context (prior day, pre-market, gap with
    dividend adjustment hook, daily ATR) and intraday features (VWAP, EMA/SMA, RSI, MACD, ATR,
    Bollinger, ROC, realized vol, relative volume by minute-of-day, opening ranges), leak-tested.
  - `strategies/library.py`: ORB, VWAP trend, VWAP reversion, gap fill, momentum breakout,
    liquidity sweep; exact parameterised rules, every signal has a stop.
  - `backtesting/engine.py` + `costs.py`: event-driven, next-open fills, gap-through stops,
    stop-first same-bar rule, trade-through limits, volume cap/partial fills, short-sale fees,
    entry window and mandatory flatten, emergency close, three cost scenarios.
  - `risk_management/risk.py`: sizing by stop distance, daily/weekly loss, streak, trade
    count, max positions, persistent kill switch with named manual reset (fails safe).
  - `analytics/metrics.py`: daily-return Sharpe/Sortino/Calmar, drawdowns, PF, expectancy,
    streaks, costs, stationary block bootstrap CIs and p-values.
  - `signal_engine/store.py`: SQLite record of every signal; refuses probabilities without a calibration source.
  - `backtesting/freeze.py` + `main.py backtest`: OOS dates refused without a frozen spec; OOS runs logged; contamination flagged.

## Blockers (need the user)

1. **Databento key**: re-enter it in the environment settings (Network secrets, host
   `hist.databento.com`, username = the `db-...` key, password blank), or confirm the
   account is active. Then run `python main.py data-coverage` and `data-quote`.
2. **Spend approval**: after quotes, approve a dollar amount for SPY 1-minute bars 2018-2026
   (expected small; within the $125 new-account credit is likely but unverified).
3. **2010-2018 equities source**: choose a vendor (FirstRate one-time, or Massive $79-199/month), or accept 2018-2026 only
   (then in-sample becomes 2018-05..2021-12 and OOS 2022+, which must be decided BEFORE any result is seen).

## Next steps (in order)

1. With a working key: `python main.py data-coverage` -> update docs/DATA_SOURCES.md with verified ranges.
2. `python main.py data-quote --start 2018-05-01 --end 2026-11-01` (and for OPRA SPY options) -> ask for approval.
3. Fetch, then `python main.py data-report ...` -> commit reports/data_quality_*.md.
4. Phase 6 research per docs/IMPLEMENTATION_PLAN.md (walk-forward runner and trial log still to write).

## Commands

```
cd trading-system
pip install -r requirements.txt
python -m pytest -q
python main.py --help
```

Repository: Astyrix/kalshi, branch `claude/wonderful-keller-cybg5g`, folder `trading-system/`.
The Kalshi paper bots in the same repository are a separate project (see the root README).
