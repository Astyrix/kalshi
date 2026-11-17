"""The four-state decision engine: WAIT_FOR_BETTER_ENTRY, ENTER_NOW,
WAIT_FOR_BETTER_EXIT, EXIT_NOW. One decision per direction per tick."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .data import Book
from .memory import Memory, bucket
from .model import Calibrator, Snapshot, price_after_move


def taker_fee(price: float, qty: int = 1) -> float:
    """Kalshi taker fee per contract: ceil(0.07 * C * P * (1-P)) to the cent, / C."""
    total = 0.07 * qty * price * (1 - price)
    return math.ceil(round(total * 100, 6)) / 100 / qty


@dataclass
class Config:
    min_p: float = 0.85           # minimum win probability to enter
    min_ev: float = 0.035         # minimum EV after fees, as a fraction of cost
    take_profit: float = 0.04     # sell once bid >= entry + fees + this ($/contract)
    stop_p: float = 0.60          # exit if win probability falls below this
    max_ask: float = 0.96         # never pay more than this (no room to exit at a profit)
    no_entry_last: int = 45       # seconds before close with no new entries
    horizon: int = 60             # seconds used for trough / peak estimates
    qty: int = 10


@dataclass
class Position:
    side: str                     # "UP" or "DOWN"
    entry: float                  # fill price incl. slippage, $/contract
    qty: int
    t: int
    key: str
    p_entry: float
    aligned: bool


def _iso(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


class Engine:
    def __init__(self, cal: Calibrator, mem: Memory, cfg: Config | None = None):
        self.cal, self.mem, self.cfg = cal, mem, cfg or Config()

    def decide(self, s: Snapshot, book: Book, pos: Position | None) -> list[dict]:
        p_up = self.cal.p_up(s)
        sides = [pos.side] if pos else ["UP", "DOWN"]
        return [self._one(s, book, side, p_up if side == "UP" else 1 - p_up, pos) for side in sides]

    # Expected excursion of the index over the horizon (mean of the running max
    # of a random walk is about 0.8 sigma sqrt(h)), nudged by momentum.
    def _excursion(self, s: Snapshot, side: str, favorable: bool) -> tuple[float, int]:
        h = min(self.cfg.horizon, max(s.tau - 5, 1))
        move = 0.8 * s.sigma * s.price * math.sqrt(h)
        sign = 1 if side == "UP" else -1
        if not favorable:
            sign = -sign
        vel = s.features["velocity"] * (1 if side == "UP" else -1)
        boost = 1 + 0.25 * max(min(vel if favorable else -vel, 2), -2)
        return s.price + sign * move * max(boost, 0.3), h

    def _one(self, s: Snapshot, book: Book, side: str, p: float, pos: Position | None) -> dict:
        cfg, out = self.cfg, None
        ask, bid = book.ask(side), book.bid(side)
        vel = s.features["velocity"] * (1 if side == "UP" else -1)
        trend = (s.features["ema_gap"] + s.features["macd_hist"]) * (1 if side == "UP" else -1)
        aligned = vel > 0 and trend > 0
        base = {
            "timestamp": _iso(s.t),
            "direction": side,
            "confidence_score": round(p, 4),
            "index_price": round(s.price, 2),
            "strike": s.strike,
            "seconds_left": s.tau,
            "bid": bid, "ask": ask,
            "simulation_metrics": {
                "historical_win_rate_ytd": round(self.mem.win_rate, 4),
                "trades": self.mem.trades,
                "recent_mistake_correction": self.mem.last_lesson(),
            },
        }

        if pos is None:
            if ask is None or bid is None:
                return {**base, "market_state": "WAIT_FOR_BETTER_ENTRY",
                        "predicted_trough_or_peak": round(s.price, 2), "execution_price": None,
                        "rationale": "No two-sided market to trade against."}
            key = bucket(s.tau, aligned, ask)
            thr = max(cfg.min_p, self.mem.threshold(key))
            fee = taker_fee(ask, cfg.qty)
            ev = p - ask - fee
            roi = ev / (ask + fee)
            trough, h = self._excursion(s, side, favorable=False)
            fair_at_trough = price_after_move(s, trough, h)
            fair_at_trough = fair_at_trough if side == "UP" else 1 - fair_at_trough
            base.update(edge=round(ev, 4), ev_roi=round(roi, 4), bucket=key, threshold=round(thr, 3))
            reasons = []
            if p < thr:
                reasons.append(f"P(win) {p:.1%} below {thr:.0%} threshold for setup {key}")
            if roi < cfg.min_ev:
                reasons.append(f"EV after fees {roi:+.1%} below +{cfg.min_ev:.1%} (ask {ask:.2f}, fee {fee:.3f})")
            if ask > cfg.max_ask:
                reasons.append(f"ask {ask:.2f} leaves no room to sell at a profit")
            if s.tau <= cfg.no_entry_last:
                reasons.append(f"only {s.tau}s left, too late to exit early")
            if vel < -1.0:
                reasons.append(f"last 60s ran {abs(vel):.1f} sigma against {side}; pullback still in progress")
            if not reasons:
                return {**base, "market_state": "ENTER_NOW",
                        "action": f"BUY {side}", "contract": s.strike,
                        "predicted_trough_or_peak": round(trough, 2),
                        "execution_price": ask,
                        "limit_price_cents": round(ask * 100),
                        "rationale": (f"P(win) {p:.1%} >= {thr:.0%}, EV {roi:+.1%} after fees, "
                                      f"momentum {'aligned' if aligned else 'neutral'} "
                                      f"(60s move {vel:+.1f} sigma). Index {s.price - s.strike:+.2f} "
                                      f"from strike with {s.tau}s left, settle sd {s.settle_sd:.2f}.")}
            return {**base, "market_state": "WAIT_FOR_BETTER_ENTRY",
                    "predicted_trough_or_peak": round(trough, 2),
                    "contract_price_at_trough": round(fair_at_trough, 3),
                    "execution_price": None,
                    "rationale": "; ".join(reasons) + (f". Expected adverse index move to {trough:.2f} "
                                                       f"({'dip' if side == 'UP' else 'spike'}) within ~{h}s; "
                                                       f"{side} contract fair value there {fair_at_trough:.2f}.")}

        # ---- holding a position ------------------------------------------
        fee_in = taker_fee(pos.entry, pos.qty)
        peak, h = self._excursion(s, side, favorable=True)
        fair_at_peak = price_after_move(s, peak, h)
        fair_at_peak = fair_at_peak if side == "UP" else 1 - fair_at_peak
        base["position"] = {"entry": pos.entry, "qty": pos.qty}
        if bid is None:
            return {**base, "market_state": "WAIT_FOR_BETTER_EXIT",
                    "predicted_trough_or_peak": round(peak, 2), "execution_price": None,
                    "rationale": "No bid to sell into; holding to settlement unless one appears."}
        fee_out = taker_fee(bid, pos.qty)
        locked = bid - pos.entry - fee_in - fee_out
        base["unrealized_pnl_per_contract"] = round(locked, 4)
        why = None
        if locked >= cfg.take_profit:
            why = f"profit target hit: selling at {bid:.2f} locks {locked:+.3f}/contract after fees"
        elif p < cfg.stop_p:
            why = f"thesis broken: P(win) fell to {p:.1%} (< {cfg.stop_p:.0%}); cutting exposure at {bid:.2f}"
        elif locked > 0 and bid >= p:
            why = f"market bid {bid:.2f} >= model value {p:.2f}; taking {locked:+.3f}/contract"
        elif locked > 0 and s.tau <= 20:
            why = f"{s.tau}s left; locking {locked:+.3f}/contract rather than riding settlement"
        if why:
            return {**base, "market_state": "EXIT_NOW", "action": "SELL_TO_CLOSE",
                    "predicted_trough_or_peak": round(s.price, 2), "execution_price": bid,
                    "rationale": why}
        return {**base, "market_state": "WAIT_FOR_BETTER_EXIT",
                "predicted_trough_or_peak": round(peak, 2),
                "contract_price_at_peak": round(fair_at_peak, 3),
                "estimated_time_to_peak_s": h,
                "execution_price": None,
                "rationale": (f"P(win) {p:.1%}, bid {bid:.2f} gives {locked:+.3f}/contract, under the "
                              f"+{cfg.take_profit:.2f} target. Expected favorable index move to {peak:.2f} in ~{h}s; "
                              f"{side} contract fair value there {fair_at_peak:.2f}.")}
