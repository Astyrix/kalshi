# Historical data: what exists, what it costs, what is verified

Status on 2026-11-24. "Verified" means checked against the vendor's own API or documentation;
"reported" means from vendor pages or third parties found by search, still to be confirmed.
Nothing has been purchased. Databento metadata calls could not be made yet: the API key saved in
the environment is rejected (HTTP 401); see PROJECT_STATUS.md.

## Databento (primary, per the project brief)

| Data | Dataset | History start | Status |
|---|---|---|---|
| SPY trades, 1-minute bars, top of book, full depth (Nasdaq venue only) | XNAS.ITCH | 2018-05-01 | reported (Databento catalog) |
| NYSE venue | XNYS.PILLAR | 2018-05-01 | reported |
| NYSE Arca venue (SPY's listing exchange) | ARCX.PILLAR | not confirmed | to verify with `main.py data-coverage` |
| US equity options (OPRA): trades, OHLCV bars, statistics (incl. open interest), definitions, CBBO-1m | OPRA.PILLAR | 2013-04-01 | reported (Databento, May 2025 announcement) |
| OPRA consolidated tick-level quotes (CMBP-1) | OPRA.PILLAR | 2023-03-28 | reported |

**Consequence: Databento cannot supply SPY share data for 2010 through April 2018.** Its
earliest US equities history is May 2018. Options trades and 1-minute quotes go back to April 2013.

Pricing (reported): historical data is pay-as-you-go, billed on the uncompressed binary
size ($/GB); new accounts get $125 of credits for historical data; historical use needs no
separate license. Exact quotes come from `metadata.get_cost`, which this project calls before
every download (`main.py data-quote`). Downloads need both `TRADESYS_MAX_SPEND_USD` and
`--approve-usd` to cover the quote.

### Single-venue caveat

XNAS.ITCH contains only trades that happened on Nasdaq. For SPY that is a minority of volume
(most SPY volume trades on other exchanges and off-exchange). A 1-minute bar from one venue
has that venue's volume and can have highs and lows a few cents off the consolidated tape.
Price-based signals on SPY are barely affected (it is the most arbitraged instrument in the
world); volume-based features (relative volume, VWAP weights) are affected in level but not
much in shape. Databento's consolidated US equities products, if available for the period,
would remove this; `data-coverage` lists them.

## Filling 2010 to April 2018 (candidates, none bought)

| Vendor | What | History | Cost (reported) |
|---|---|---|---|
| FirstRate Data | SPY 1-minute bars, split/dividend adjusted versions | from 2000s | one-time purchase |
| Massive (formerly Polygon.io) | consolidated minute aggregates, API and flat files | Developer plan 10 years; Advanced all history | $79/month (Developer), $199/month (Advanced) |
| Tick Data | 1-minute trade and quote bars | from 2004 | quote |
| AlgoSeek | US equities, options intraday | from 2007 | quote |

Combining sources without inconsistency: use one vendor's consolidated bars for the whole
2010-2026 span if possible (Massive or FirstRate), and Databento for what only it has
(trade-level and quote data from 2018, options). If two sources must be stitched, overlap
them for at least 6 months (2018-05 onward), compare bar by bar (open/high/low/close
differences, volume ratio), document the differences in the data-quality report, and never
switch source inside a train/test split. Use unadjusted prices for intraday logic
(splits: SPY has none; dividends: the ex-date gap is handled by `gap_adj`).

## Options history (SPY expirations, reported)

From a Cboe table cited in an NYU Stern study: SPY Friday weeklies from 2010-06-11,
Wednesday expirations from 2016-08-30, Monday from 2018-02-16, Tuesday and Thursday from
2022-11-14 and 2022-11-16. So **daily 0DTE SPY options exist only from mid-November 2022**;
before that, 0DTE exists only on expiration weekdays. The options engine will read the actual
expirations from OPRA instrument definitions, never from this table.

## Context data still to source

VIX (Cboe publishes daily history free; intraday via a vendor), ES futures (Databento
GLBX.MDP3, from 2010), QQQ (same as SPY), Treasury yields (FRED / Treasury, daily, free),
economic calendar with release times (BLS, Fed and BEA schedules; first-release values only,
never revised data), SPY dividend ex-dates (needed for `gap_adj`).

Sources: [Databento US equities catalog](https://databento.com/catalog/us-equities),
[Databento datasets](https://databento.com/docs/datasets),
[OPRA.PILLAR](https://databento.com/docs/datasets/opra-pillar),
[Databento pricing and credits FAQ](https://databento.com/docs/faqs/usage-pricing-and-data-credits),
[Databento HTTP authentication](https://databento.com/docs/api-historical/basics/authentication),
[FirstRate Data SPY](https://firstratedata.com/i/etf/SPY),
[Massive minute aggregates](https://massive.com/docs/flat-files/stocks/minute-aggregates/2026/09),
[Tick Data](https://www.tickdata.com/product/us-equities/),
[NYU Stern study (SPY expiration history)](https://www.stern.nyu.edu/sites/default/files/2026-06/Glucksman_SiddheshDalvi_HenryLaFond.pdf),
[Cboe: evolution of same-day options](https://www.cboe.com/insights/posts/the-evolution-of-same-day-options-trading).
