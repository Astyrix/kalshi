# Audit: day-trader-skill (Jakeschincariol/day-trader-skill)

Upstream commit `3d0f4ef` ("The Trader skill v2: 11 day-trading skills"), MIT license.
Installed project-locally in `.claude/skills/trader-*` (at the repository root), a copy of
the license and README in `third_party/day-trader-skill/`.

## What was checked

- All 315 of its own unit tests pass offline (`python -m unittest discover -s tests`).
- Every tool starts (`--help`) from the installed location.
- Network and execution surface: the tools read public endpoints (Yahoo Finance's unofficial
  chart API, Nasdaq Trader symbol files, SEC EDGAR, the House Clerk) with `urllib`; one
  `subprocess` call (`pdftotext` on a downloaded PDF, fixed arguments, no shell). No `eval`,
  `exec` or `pickle`. No credentials in code. Nothing places an order: `trader-broker` only
  renders a ticket for Claude's broker connector, after `trader-risk` passes and the user types yes.
- Dependencies: standard library only (nothing outdated to patch).

## Findings that matter for this project

| # | Finding | Impact | What tradesys does instead |
|---|---|---|---|
| 1 | NYSE holidays and early closes are a hand-kept list for **2026-2027 only** (`CALENDAR_YEARS = (2026, 2027)`) | Unusable for 2010-2025 backtests; every earlier holiday would look like a missing-data day | `exchange_calendars` XNYS (includes Hurricane Sandy 2012, the 2018 and 2025 days of mourning) |
| 2 | DST is computed by a hand-written rule (2nd Sunday of March to 1st Sunday of November) | Correct only for 2007 onward; fragile | `zoneinfo` America/New_York |
| 3 | The entry timer reports the trigger bar's **close** as the entry price | A close is already in the past when the signal is known: optimistic by about half a bar | Fill at the **next bar's open** plus half spread and slippage |
| 4 | No spread, slippage, commissions or fees anywhere in the replay or history check | Overstates every result, most for short holds | Three cost scenarios (normal / unfavorable / extreme), SEC and FINRA fees |
| 5 | The Time Machine draws each 5-minute bar independently by time of day from the last ~60 sessions | Destroys autocorrelation and volatility clustering; 60 days is one regime; its "odds" are not calibrated probabilities | Event-driven replay on real history; block bootstrap over days for uncertainty |
| 6 | Its history check reruns a rule on ~59 sessions | Far too few trades for a conclusion; no multiple-testing control | 2010-2019 in-sample with walk-forward, frozen out-of-sample 2020+ |
| 7 | Data is Yahoo's unofficial endpoint (can lag, change or rate-limit) | Not a research-grade source | Databento (licensed), cached as Parquet with a data-quality report |
| 8 | ORB entry timer is long-only by default | Ignores the bearish half of opportunities | Long and short |
| 9 | Stop-first rule when one bar touches both stop and target | **Correct and conservative** | Kept |
| 10 | Risk checklist (max loss, daily loss, trades per day, R:R, entry window, size, cash) and explicit human approval before any order | **Sound** | Kept and extended (weekly loss, losing streak, persistent kill switch) |

## Reused

The ideas, not the code: the approval-before-order workflow, the conservative same-bar
stop rule, the risk checklist, the plan/entry/exit vocabulary (R multiples, opening-range
midpoint stops). The skill tools stay installed for interactive use (news, scanner, alerts),
but no tradesys result depends on them.
