import math

from kbot.data import Book, Window
from kbot.memory import Memory
from kbot.model import Calibrator, snapshot
from kbot.strategy import Config, Engine, Position, taker_fee
from kbot import backtest


def walk(start, n, step, t0=1_000_000):
    return [(t0 + i, start + i * step) for i in range(n)]


def test_taker_fee_matches_kalshi_formula():
    # 0.07 * 10 * 0.5 * 0.5 = $0.175 -> rounds up to $0.18 total
    assert math.isclose(taker_fee(0.50, 10), 0.018)
    assert math.isclose(taker_fee(0.90, 1), 0.01)


def test_prior_is_confident_far_above_strike_late():
    idx = walk(100_000, 900, 0.0)
    close = idx[-1][0] + 30
    s = snapshot(idx, strike=99_000, close_ts=close, t=idx[-1][0])
    assert s.p0 > 0.99


def test_prior_near_half_at_strike():
    idx = [(1_000_000 + i, 100_000 + (5 if i % 2 else -5)) for i in range(900)]
    s = snapshot(idx, strike=100_000, close_ts=idx[-1][0] + 600, t=idx[-1][0])
    assert 0.4 < s.p0 < 0.6


def test_no_lookahead():
    idx = walk(100_000, 900, 1.0)
    t = idx[400][0]
    s = snapshot(idx, 100_000, idx[-1][0], t)
    assert s.price == idx[400][1]


def test_enter_then_exit_states():
    eng = Engine(Calibrator(a=1.0, c=0.0), Memory(), Config())
    idx = walk(100_000, 900, 0.05)
    close = idx[-1][0] + 300
    s = snapshot(idx, 99_900, close, idx[-1][0])
    up = eng.decide(s, Book(yes_bid=0.80, yes_ask=0.81), None)[0]
    assert up["direction"] == "UP" and up["market_state"] == "ENTER_NOW"
    pos = Position("UP", 0.81, 10, s.t, up["bucket"], up["confidence_score"], True)
    hold = eng.decide(s, Book(yes_bid=0.82, yes_ask=0.83), pos)[0]
    assert hold["market_state"] == "WAIT_FOR_BETTER_EXIT"
    out = eng.decide(s, Book(yes_bid=0.90, yes_ask=0.91), pos)[0]
    assert out["market_state"] == "EXIT_NOW"


def test_wait_when_ev_too_thin():
    eng = Engine(Calibrator(a=1.0, c=0.0), Memory(), Config())
    idx = walk(100_000, 900, 0.05)
    s = snapshot(idx, 99_900, idx[-1][0] + 300, idx[-1][0])
    d = eng.decide(s, Book(yes_bid=0.98, yes_ask=0.99), None)[0]
    assert d["market_state"] == "WAIT_FOR_BETTER_ENTRY"


def test_backtest_runs_and_settles():
    t0 = 1_000_000
    idx = walk(100_000, 1200, 0.05, t0)
    w = Window(event="E", ticker="E-1", strike=100_000, open_ts=t0 + 300, close_ts=t0 + 1200,
               result="yes", settle_value=100_060, index=idx,
               quotes=[(t0 + 300 + 60 * k, 0.70, 0.72, 100.0) for k in range(1, 15)])
    summ = backtest.run([w], Calibrator(a=1.0, c=0.0), Memory(), Config())
    assert summ["windows"] == 1
    assert summ["trades"] <= 1
