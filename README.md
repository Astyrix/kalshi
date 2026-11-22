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

Other coins (`python -m kbot.calibration --coins BTC,ETH,SOL,XRP --days 30`):
a model-free check of every minute's ask against how often that side won,
after ask + 1c and fees, finds no price / time-left bucket with an edge for
any of BTC, ETH, SOL or XRP (last 30 days, ~2,800 markets each, 1c median
spread). Buying and holding loses 0-7c per contract almost everywhere; the
few slightly positive buckets are within noise (|z| < 1).

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

## What real traders do (Nov 2026 study)

To find what actually works in these markets, the bot studied real traders
on both venues that list the same 15-minute BTC windows:

| Study | Command | Finding |
|---|---|---|
| Kalshi tape, 150 markets, 639M contracts | `python -m kbot.tape_study` | Takers lose 0.12c/contract before fees, so makers keep ~0.1c. No group of takers (by size, price, time left) is significantly better or worse; trades of 1000+ contracts are not informed. The fee (up to 1.75c) is the taker's real loss. KXBTC15M's fee type is `quadratic` (no maker fee). |
| Spot latency, 150 markets | `python -m kbot.latency_study` | Kalshi YES moves in the same second as OKX spot (corr 0.48 at lag 0, 0.06 at +1 s). The latency-arbitrage window is under a second: only bots sitting next to the exchange can trade it. |
| Late favourites / long shots, 32k markets | `kbot/calibration.py --fine --slip 0 --qty 100` | Buying at the ask loses on both sides; long shots are the most overpriced (favourite-longshot bias, up to -6c), but that has shrunk a lot in recent months. |
| Live market making | `python -m kbot.maker_paper` | Resting bids at the back of the queue lost $41.60 over 3 markets even with no fee: we only get filled when the price runs through our quote. |
| Polymarket wallets, 14 days, 8,677 wallets, $41M | `python -m kbot.poly_study` | Taker wallets lost $74k in total. Pair buyers (both outcomes for < $1) make 2-3% gross, which Polymarket's ~2.5% fee cancels. |
| What winners do differently, 7 days | `python -m kbot.poly_deep` | Winners buy with the last 30 s of movement, and fair value moves their way afterwards (+0.9c at 120 s). Losers buy against it (-3.2c at 120 s) and lose 7.5c/share. A simple spot fair-value model is anti-predictive: the market knows more. |
| Momentum, Kalshi tape, 150 markets | (scratch study) | Takers buying with the last 5 s of spot movement do ~0.8c/contract better than those buying against it (z 4.5), but the taker fee (~1.5c mid-range) exceeds it: every group loses after fees. |
| **Cross-venue pairs** | `python -m kbot.xvenue_watch`, `python -m kbot.basis_study --hist` | Buy one outcome on Polymarket and the other on Kalshi when the pair costs < $1 after both fees. It pays $1 unless the venues settle differently. Positive expected value only after filtering out the split risk, and only a little is left for a bot that is not the fastest; see the next section. |

### Basis risk: when the two venues settle differently

Polymarket settles on Chainlink BTC/USD, Kalshi on CF BRTI, and each venue
takes its strike from its own feed at the open. Over 1,326 windows settled
on both (`python -m kbot.basis_study`):

- They split in 9 windows (0.68%), and only when BRTI closed within $10 of
  Kalshi's strike (17.5% of windows that closed within $5, 4.9% at $5-10,
  none further away). "The BRTI move is smaller than the change in the
  Chainlink-BRTI gap over the window" explains 100% of the splits.
- The gap (median +$1, 5-95% range -$6 to +$9) changes by a median $2.27
  in a window (99th percentile $13). It mean-reverts: correlation -0.45
  between its level at the open and its change.
- A split is not a coin flip. A pair (Polymarket Up + Kalshi NO) is worth
  exactly $1 + P(Polymarket Up) - P(Kalshi YES), so it looks cheap exactly
  when the venues expect their feeds to disagree. In the one live split
  window the pairs looked 28-89c underpriced in the last minute, and every
  one of them lost both legs.

On the historical tapes (every second a Polymarket taker and a Kalshi taker
bought opposite sides, 155 windows, split windows reweighted to 0.68%;
`python -m kbot.basis_study --hist`), expected P&L per window at up to 500
pairs, filling at the signal vs. arriving 2 s late:

| Rule | At signal | Loss in a split window | Break-even split rate | 2 s late, limit (edge >= 0) | 2 s late, any price |
|---|---|---|---|---|---|
| edge >= 1c after fees | +$3.93 | -$247 | 2.2% | -$0.13 | -$3.78 |
| edge - P(split) >= 0.5c | +$4.81 | -$191 | 3.1% | +$0.15 | -$2.60 |
| edge >= 0.5c and P(BRTI ends within $15 of strike) < 10% | +$2.93 | -$9 | 25% | +$0.46 (+0.8c/pair) | -$1.41 |

Two conclusions. The zone rule (skip when BRTI may close near the strike)
gives up some expected value to nearly remove the tail. And the gaps close
within a second or two: matched historical fills were taken by someone,
possibly arbitrageurs doing both legs, and a bot that arrives 2 s later
keeps only about +$0.5 a window, and only with limit orders. That is a
small edge that belongs to the fastest bots. The rule was chosen from about
30 variants on the same windows, so the live recorder
(`kbot/xvenue_watch.py`: reads both books every second, logs BRTI, strike
and volatility, and reports fills 1-2 reads late with a limit) is the real
test. Live so far (35 settled windows, one split; per window, expected value
uses the historical 0.68% split rate):

| Rule (edge >= 0.5c), live | Normal windows | The split window | Expected |
|---|---|---|---|
| at signal | +$5.94 | -$111 | +$5.14 |
| zone < 10%, at signal | +$2.63 | -$21 | +$2.47 |
| filled 1 read later, limit | +$2.13 | -$98 | +$1.45 |
| zone < 10%, 1 read later, limit | +$0.71 | -$49 | +$0.38 |
| zone < 10%, 2 reads later, limit | +$0.42 | +$19 | +$0.54 |

Not one of the 34 normal windows lost money under any rule, and the zone
rule's late fills match history (+$0.4-0.7 vs +$0.46). Late fills without
the zone rule did better live than on the tapes (+$2.13 vs about $0), so the
zone filter may be giving up too much. It only looks at BRTI against
Kalshi's strike; a split needs the other feed on the other side of its
own strike. The recorder now also logs Polymarket's live Chainlink price
(its real-time data socket) and the window's Chainlink strike, so the split
risk can be measured from both feeds instead of guessed from one.

Polymarket's strike (priceToBeat) and final price are not the Chainlink print
at the open and close. They match its 60-second average before them (to
about $0.60 over the first windows checked; the print at the open was $10-45
off). So both venues settle on a 60-second average of their own feed, and a
window's final price is always the next window's strike (1,338 of 1,338).

Can the split be predicted per pair? A split needs the final BRTI move to land
between the two feeds' distances to their strikes, so the sign of the final
gap between those distances says which pair loses. Predicting that gap from
the open (it mean-reverts: -0.40 x the strike offset, residual sd $3.61)
pointed the wrong way in both live splits (predicted -$2.1 and -$1.4, actual
+$12.4 and +$1.9), and filtering pairs on it did worse than the zone rule
(58 windows). Within a window the gap is steadier (sd $2-3, about $2 of
change left to the close), so the recorder now logs it live; it has not seen
a split yet.

Polymarket restricts some regions, including the US on its main exchange;
check your own eligibility before considering it. Fill risk (both legs
filling at the quoted prices) is not tested by paper trading.

### Same-venue box: the 15-minute window vs Kalshi's hourly markets

The 15-minute window that closes on the hour and Kalshi's hourly "BTC above
$X" markets (KXBTCD, strikes $100 apart) settle on the same number, the
60-second BRTI average before the hour. So in the last 15 minutes of every
hour:

- 15m YES + hourly NO at the next strike up pays $1, or $2 if BTC settles
  between the two strikes;
- 15m NO + hourly YES at the next strike down, likewise.

That pays at least $1 whatever BTC does, with no second venue and no basis
risk. On the tapes of 164 hour closes (7 days, `python -m kbot.hourly_watch
--hist`), takers bought both legs for under $1 after fees in the same second
in 89 closes: +$7.34 an hour at 50 boxes per entry, never a losing hour.
Arriving 1 s later with a limit at those prices, takers traded both legs
again at or below them in only 27 closes (+$0.23 an hour). That is a
pessimistic test (a quote can rest without trading), so the live recorder
(`python -m kbot.hourly_watch`) reads both live order books every 1.5 s in
the last 15 minutes of each hour. Note: the per-market bid/ask on Kalshi's
events listing is a snapshot 15-20 s stale; only `/orderbook` is live. The
first live hour, read from that snapshot, showed a $0.985 box that was not
real.

## How it works

- **Data (`kbot/data.py`).** The BRTI index Kalshi settles on, second by
  second; Kalshi quotes, book and trades. Requests are rate-limited (0.3 s
  apart by default).
- **Model (`kbot/model.py`).** A physics prior: settlement is the 60-second
  average of BRTI before close compared with the strike. MaxyCharts' index
  already is that average (its print at the close equals the settlement in
  every cached window), so P(UP) = P(index now + remaining move > strike),
  with the remaining move drawn from an empirical table of standardized final
  moves fitted on the oldest 60 days (`state/brti-z.json`), and volatility
  from 60-second changes of the averaged index. The earlier prior averaged
  the average again and measured volatility from smoothed 1-second changes,
  which made it badly overconfident (with 5 minutes left it said < 2% or
  > 98% in 58% of windows and was wrong 8% of the time there). Out of
  sample the fixed prior cuts log-loss by a third or more at every horizon,
  but it does not change the scalp model's results: in a walk-forward A/B
  over Jul-Nov 2026 with identical rows except the two prior columns, the
  correlation of predicted with realized P&L was +0.0971 (old) vs +0.0973
  (fixed), with a handful of trades either way. LightGBM had already learned
  around the overconfident input from time left and strike distance. An online logistic layer then
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
