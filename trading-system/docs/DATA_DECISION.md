# Data decision

No Databento account is available, so the research runs on free data.

## Revision 3 (2026-11-24, no out-of-sample result has been seen)

Dukascopy throttles this environment to a handful of files an hour (HTTP 429), so the
holdout moves to HistData, which now covers 2010-11 .. 2026-11-13 from one vendor
(histdata.com yearly and monthly M1 files):

| Role | Source | Span |
|---|---|---|
| In-sample (development) | Oanda SPX500_USD | 2010-01-04 .. 2019-12-31 |
| In-sample replication | HistData SPXUSD | 2010-11-15 .. 2018-12-31 |
| Out-of-sample (holdout) | **HistData SPXUSD** | 2020-01-02 .. 2026-11-13 |
| Second holdout source (if its download completes) | Dukascopy USA500.IDX | 2020-01-02 .. |

No strategy has passed in-sample, so nothing has been frozen and the holdout is untouched.

## Revision 2 (2026-11-24, before any result on Oanda or Dukascopy was seen)

Dukascopy's USA500.IDX minute files from late September 2011 onward are empty placeholders
(flat prices, zero volume), and the vendor throttles to roughly one file every 10-50 seconds,
so it cannot be the in-sample source. Oanda SPX500_USD minute bars (tick volume, UTC, 2009-01 to
2020-05) from the same public GitHub mirror cover the whole brief's in-sample decade.

| Role | Source | Span | Notes |
|---|---|---|---|
| In-sample (development, walk-forward) | Oanda SPX500_USD 1-minute | 2010-01-04 .. 2019-12-31 | S&P 500 CFD; tick volume; UTC (verified from the opening-bell volume spike) |
| Independent replication | HistData SPXUSD 1-minute | 2010-11-15 .. 2018-12-31 | no volume; EST without DST, shifted to UTC |
| Out-of-sample (holdout, one run on a frozen spec) | Dukascopy USA500.IDX/USD 1-minute bid | 2020-01-02 .. 2026-11-23 | S&P 500 CFD; tick volume |
| Vendor consistency | Oanda vs Dukascopy | 2020-01 .. 2020-05 overlap | compare before the holdout run |
| Real-SPY check | Yahoo SPY 1-minute vs Dukascopy | last ~30 days | done: reports/proxy_vs_spy.md |
| Daily context | Yahoo SPY, ^GSPC, ^VIX daily; SPY dividends | 1993+ | |

The brief's split is kept exactly: in-sample 2010-2019, out-of-sample 2020 onward. The cost of
free data: the in-sample and out-of-sample vendors differ (both are S&P 500 CFDs that track
the index; the overlap check quantifies the difference), volume is tick activity, not SPY shares.

## Revision 1 (superseded)

Dukascopy for both periods (2011-09 .. 2026). Abandoned for the reasons above. The only
study run under revision 1 was on HistData, which is in-sample only.

## Conversion to SPY dollars

Index prices are scaled per session by the previous session's SPY / S&P 500 close ratio
(`free_sources.spy_equivalent`), so stops, targets, the $0.01 tick and the cost model are in
SPY terms. The proxy cannot show SPY's own spread, share volume or opening-auction effects.
