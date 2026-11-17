# kbot: Kalshi 15-minute crypto paper trader

A research bot for Kalshi's 15-minute crypto up/down markets (`KXBTC15M` and
the others). It reads live and historical data from
[maxycharts.com](https://maxycharts.com), estimates the probability that each
side wins, and on every tick outputs one of four states per direction:

| State | Meaning |
|---|---|
| `WAIT_FOR_BETTER_ENTRY` | No edge yet, or price is still moving against the side; reports the expected adverse move |
| `ENTER_NOW` | P(win) ≥ threshold (85% by default) **and** EV after fees ≥ +3.5% |
| `WAIT_FOR_BETTER_EXIT` | Holding; profit target not reached; reports the expected favorable move |
| `EXIT_NOW` | Profit target hit, market overpaying vs the model, thesis broken, or close is near |

**It never places real orders.** Live mode is paper trading only.

## Install

```bash
pip install requests pytest
```

## Use

```bash
python -m kbot backtest --days 14          # walk-forward backtest; learns as it goes
python -m kbot backtest --days 365         # full year (~35k windows, several hours to download once)
python -m kbot live                        # paper-trade the current market, JSON per tick
python -m kbot live --quiet-waits          # only print ENTER/EXIT and fills
python -m kbot --coin ETH backtest --days 7
```

Thresholds: `--min-p 0.85 --min-ev 0.035 --take-profit 0.04 --stop-p 0.60 --qty 10`.

Backtests write `reports/backtest-*.json` and `reports/trades-*.csv`. Learned
weights and memory go to `state/`, and live mode loads them from there.
Downloaded history is cached in `data/cache/` so each window downloads once.

## How it works

- **Data (`kbot/data.py`).** The BRTI index Kalshi settles on, second by
  second; Kalshi quotes, book and trades. Requests are rate-limited (0.3 s
  apart by default).
- **Model (`kbot/model.py`).** A physics prior: settlement is the 60-second
  average of BRTI before close compared with the strike, so P(UP) comes from
  distance to strike against volatility over the time left (the part of the
  average already printed is known exactly). An online logistic layer then
  blends that prior with the market's own price and RSI-14, MACD histogram,
  EMA 9/21 gap, 60-second velocity and book imbalance, learning the weights
  from outcomes.
- **Engine (`kbot/strategy.py`).** The four states, Kalshi taker fees
  (`ceil(0.07·C·P·(1−P))`), and trough/peak estimates from the expected
  excursion of a random walk over the next 60 s, scaled by momentum.
- **Memory (`kbot/memory.py`).** Every trade gets a post-mortem
  (`premature_entry`, `early_exit`, `false_breakout`, `shaken_out`,
  `thesis_failed`, plus missed opportunities). Setups are bucketed by time
  left, momentum and price; a bucket that loses money gets a stricter entry
  threshold.
- **Backtest (`kbot/backtest.py`).** Walk-forward: each decision uses only
  data printed before it, and the model updates only after a window settles.
  Buys fill at the ask + 1¢, sells at the bid − 1¢, fees on both sides.

## Limitations

- Historical order-book depth isn't available, only the 1-minute
  top-of-book close. Backtest fills assume your size fits at the touch and can
  only happen once a minute, and the imbalance feature is 0 in backtests.
- maxycharts.com is a free third-party site with no documented API. It can
  change or block clients at any time.
- The trough and peak numbers are statistical expectations, not exact
  predictions.
- The headline number to check is the Brier score in the report: the model
  must beat `market_mid` to have a real edge.
