# tradesys: SPY 9:30-11:00 intraday research and trading system

A research-first system to test whether SPY (and later SPY options) has a tradeable edge in
the first 90 minutes of the US session, after realistic costs. It backtests event by event,
keeps a frozen out-of-sample period (2020 onward), and paper-trades only after validation.
**Live trading is disabled and will stay disabled without explicit approval.**

Status, blockers and next steps: [PROJECT_STATUS.md](PROJECT_STATUS.md).
Plan: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).
Data: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md). Skill audit: [docs/SKILL_AUDIT.md](docs/SKILL_AUDIT.md).

## Install

```bash
cd trading-system
python -m pip install -r requirements.txt       # Python 3.11+; pinned versions
cp .env.example .env                            # put DATABENTO_API_KEY in your environment, never in code
python -m pytest -q
```

## Use

```bash
python main.py data-coverage                                       # what Databento has (free metadata)
python main.py data-quote --start 2018-05-01 --end 2026-11-01      # what a download would cost
TRADESYS_MAX_SPEND_USD=10 python main.py data-fetch --start 2018-05-01 --end 2026-11-01 --approve-usd 10
python main.py data-report --start 2018-05-01 --end 2026-11-01     # data-quality report -> reports/
python main.py backtest --strategy orb --params '{"minutes": 15, "target_r": 2}' --start 2018-06-01 --end 2019-12-31
python main.py freeze --strategy orb --params '{"minutes": 15, "target_r": 2}' --note "why"
python main.py backtest --frozen config/frozen/orb-<id>.json --start 2020-01-01 --end 2026-11-01
python main.py kill-switch --status
```

## How a backtest executes (the rules that keep it honest)

- Bars are 1-minute, labelled by start time, in America/New_York (DST via zoneinfo); sessions,
  holidays and early closes from the NYSE calendar.
- A strategy sees only the row of the bar that just closed. Its order fills at the **next
  bar's open** plus half the spread plus slippage.
- New entries from 9:31 to 10:50; everything is closed at the 10:55 open; flat by 11:00.
  Anything left open by missing data is closed as an emergency at extreme costs.
- Stops gap: a bar opening past the stop fills at the open. A bar touching both stop and
  target is a stop. A profit target (limit) fills only after the price trades one tick through it.
- Fills are capped at 10% of the bar's volume (partial fills), commissions per share with a
  minimum, SEC and FINRA fees on sales, three cost scenarios (normal, unfavorable, extreme).
- Risk: 0.25% of equity at risk per trade, 1% daily and 3% weekly loss limits, 3 losses in a
  row halt the day, one position, five trades a session, a persistent kill switch that needs a
  named manual reset.

## Layout

```
config/            default.yaml (all parameters), frozen/ (frozen strategy specs, OOS run log)
src/tradesys/      data_ingestion, feature_engineering, strategies, backtesting, risk_management,
                   analytics, signal_engine, market_regimes*, options*, execution*, monitoring*
tests/             unit and integration tests (synthetic bars test mechanics only)
docs/ reports/     plan, data sources, audits; data-quality and performance reports
main.py            command line
```
`*` = planned, empty until its phase (see the plan). Market data under `data/` is never
committed (vendor licence).

Not financial advice. Most intraday strategies do not survive costs; this system is built to
find that out before money is at risk.
