import datetime as dt

import pytest

from tradesys.risk_management.risk import KillSwitch, RiskManager


def test_sizing(cfg):
    rm = RiskManager(cfg.risk)
    assert rm.size(500.0, 499.0, 0.0) == 200                  # $250 at risk / $1.00 a share
    assert rm.size(500.0, 500.0, 0.0) == 0
    assert rm.size(500.0, 499.99, 0.0) == 200                 # capped by 1x leverage: 100k / 500


def test_streak_and_weekly_limit(cfg):
    rm = RiskManager(cfg.risk)
    rm.start_session(dt.date(2024, 6, 10))
    for _ in range(3):
        rm.on_entry(); rm.on_exit(-10)
    assert not rm.can_enter()[0]
    rm.start_session(dt.date(2024, 6, 11))
    assert rm.can_enter()[0]                                  # streak resets next session
    rm.on_entry(); rm.on_exit(-3100)                          # > 3% of the week's start
    rm.start_session(dt.date(2024, 6, 12))
    ok, why = rm.can_enter()
    assert not ok and "weekly" in why
    rm.start_session(dt.date(2024, 6, 17))                    # new ISO week
    assert rm.can_enter()[0]


def test_kill_switch_persists_and_needs_named_reset(tmp_path, cfg):
    p = tmp_path / "ks.json"
    KillSwitch(p).trip("stale data")
    rm = RiskManager(cfg.risk, KillSwitch(p))                 # a restart reads the file
    rm.start_session(dt.date(2024, 6, 10))
    ok, why = rm.can_enter()
    assert not ok and "stale data" in why
    with pytest.raises(ValueError):
        KillSwitch(p).reset(" ")
    KillSwitch(p).reset("operator")
    assert rm.can_enter()[0]


def test_unreadable_kill_switch_fails_safe(tmp_path):
    p = tmp_path / "ks.json"
    p.write_text("{not json")
    assert KillSwitch(p).tripped
