import datetime as dt

import pandas as pd

from tradesys.market_calendar import is_early_close, is_session, session_close, sessions, window


def test_special_closures_and_holidays():
    assert not is_session(dt.date(2012, 10, 29)) and not is_session(dt.date(2012, 10, 30))   # Hurricane Sandy
    assert not is_session(dt.date(2018, 12, 5))                                              # G.H.W. Bush
    assert not is_session(dt.date(2025, 1, 9))                                               # J. Carter
    assert not is_session(dt.date(2022, 6, 20))                                              # Juneteenth observed
    assert is_session(dt.date(2021, 6, 18))                                                  # before Juneteenth existed


def test_early_close():
    assert session_close(dt.date(2024, 7, 3)) == dt.time(13, 0) and is_early_close(dt.date(2024, 11, 29))
    assert session_close(dt.date(2024, 7, 5)) == dt.time(16, 0)


def test_window_uses_new_york_time_across_dst(cfg):
    before, after = window(dt.date(2024, 3, 8), cfg.session), window(dt.date(2024, 3, 11), cfg.session)
    assert before.open.tz_convert("UTC").hour == 14 and after.open.tz_convert("UTC").hour == 13
    assert before.hard_flat.time() == dt.time(11, 0) and before.entry_cutoff.time() == dt.time(10, 50)


def test_session_count_is_plausible():
    n = len(sessions(dt.date(2010, 1, 1), dt.date(2019, 12, 31)))
    assert 2510 <= n <= 2520
