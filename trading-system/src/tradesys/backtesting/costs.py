"""Transaction costs for SPY shares.

Bars are built from trades, so a market buy is assumed to pay the trade price plus half the
quoted spread plus slippage; a market sell receives the trade price minus the same. Limit
orders (profit targets) fill at their price with no spread or slippage, but only once the
market trades through them (see engine). Commissions per share with a minimum; SEC Section 31
and FINRA TAF fees on sales. Rates are configuration, not constants: they change over time.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import CostCfg, CostScenario


@dataclass(frozen=True)
class CostModel:
    scen: CostScenario
    sec_fee: float
    taf: float
    taf_max: float
    participation_cap: float
    latency_bars: int = 0

    @classmethod
    def from_cfg(cls, cfg: CostCfg, scenario: str = "normal") -> "CostModel":
        return cls(cfg.scenarios[scenario], cfg.sec_fee_per_dollar_sold, cfg.finra_taf_per_share_sold,
                   cfg.finra_taf_max, cfg.participation_cap, cfg.latency_bars)

    def market_fill(self, side: int, ref: float) -> float:
        """side +1 buys, -1 sells; ref is the trade price at the time (a bar's open)."""
        return ref + side * (self.scen.half_spread + self.scen.slippage)

    def stop_fill(self, side: int, stop: float, bar_open: float) -> float:
        """A stop becomes a market order when touched; across a gap it fills at the open."""
        ref = min(stop, bar_open) if side < 0 else max(stop, bar_open)
        return self.market_fill(side, ref)

    def commission(self, shares: int) -> float:
        return 0.0 if shares <= 0 else max(self.scen.min_commission, shares * self.scen.commission_per_share)

    def sale_fees(self, shares: int, price: float) -> float:
        return self.sec_fee * shares * price + min(self.taf * shares, self.taf_max)

    def round_trip_per_share(self, price: float, shares: int = 100) -> float:
        """Estimated cost per share of entering and exiting at market (used for sizing)."""
        spread_slip = 2 * (self.scen.half_spread + self.scen.slippage)
        fixed = 2 * self.commission(shares) + self.sale_fees(shares, price)
        return spread_slip + fixed / max(shares, 1)
