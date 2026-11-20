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
pip install -r requirements.txt
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

## Day-trader scalp

```bash
# train the live model on everything cached (about 5 minutes)
python -m kbot.trader --days 365 --drop flow,calendar --lean-candles --real-fills --save-model state/trader-model.pkl --no-walk
# paper-trade live: once a minute, only contracts at or under 25c, 1c minimum predicted edge
python -m kbot paper --margin 0.01 --max-price 0.25
python -m kbot paper-report                 # results, by price / exit / setup / pattern
```

The model (`kbot/trader.py`) learns, for each side and six take-profit/stop-loss
pairs, the P&L of "buy at the ask, sell at the bid once +TP or -SL is reached,
else sell a minute before close". It combines probability vs price, volatility
for the hour, regime (trend/range/chop), price action (VWAP, session
high/low, swing support/resistance, round numbers) and 1m/5m/15m candles and
patterns. Order flow and the economic calendar are left out: removing them
improved out-of-sample results twice.

The paper trader (`kbot/paper.py`) runs that model on the live market. It
sells by watching the bid (no resting orders), logs what a 5-second-checked
exit and the depth at the best ask would have been, keeps open positions
across restarts, saves newly settled markets and retrains once a day.

### Current status: no edge found after costs

An earlier version of the backtest had look-ahead bias: it dropped every
minute where one side of the book was empty, including minutes after the
decision. A cheap contract that was dying goes one-sided, so its entry
minute looked like the last quote and was skipped, and losing trades
quietly vanished (cheap late moments in the table won 10.3%, the ones left
out 0.1%). Every profitable backtest reported before the fix (+$250 to
+$1,465) was inflated by this. Fixed in `trader.build` / `all_quotes`, with
a regression test.

With the fix, the 10-month walk-forward paper trade
(`python -m kbot.history_paper`, Feb-Nov 2026, model retrained monthly on
earlier data, fills checked against real trades, ask + 1c in, bid - 1c out,
fees both ways):

| Rules | Trades | P&L |
|---|---|---|
| <=25c, 1c edge | 16 | +$3.05 |
| <=25c, 2c edge | 5 | +$0.90 |
| any price, 1c edge | 62 | -$12.91 |
| any price, 2c edge | 18 | -$2.66 |

The model rarely predicts an edge bigger than the spread, slippage and
fees, and the few trades it takes break even. Cheap contracts in the last
minutes win at about their price (about 3.5% won at about 3.4c with 2
minutes left). Live paper trading agrees (29 trades, 3 wins, about -$2.70).

Costs decide it (`python -m kbot.experiments --days 346 --only costs,maker`,
last ~5.5 months, walk-forward): filling exactly at the quote with fees gives
a thin +$192 (0.4c per contract, 16 of 25 weeks up); with no fees at all,
+$1,143. Resting maker orders (buy at our bid, take profit with a resting
sell, maker fee 1/4 of taker) show +$165 when a fill only needs a trade
below our price, but about $0 once the price must trade 1c through our
level - the profit depends on queue position this bot would not have.

What still holds: real trades at or better than the 1-minute quote happen
within 2 seconds (`python -m kbot.fill_check --any-side --window 2`; queries
over 30-60 s miss this because the trade API returns only the newest 1000
trades). Spot (Coinbase) leads the MaxyCharts index by about 30 s, but
Kalshi prices already reflect spot. Order flow and calendar inputs did not
help.

Patterns (`python -m kbot.pattern_study`): on the MaxyCharts index, 1-minute
patterns look predictive, but that index lags Coinbase spot by about 30 s, so
the "pattern" is the average catching up. On real Coinbase candles almost no
pattern predicts direction, and Kalshi prices don't lag spot. Breakouts do
predict bigger-than-normal moves (1.2-1.4x) on both. `kbot/pattern_memory.py`
keeps scoring every pattern live and warns when one stops working.

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
