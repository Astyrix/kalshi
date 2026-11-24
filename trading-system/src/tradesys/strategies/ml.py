"""Statistically derived direction model (brief item 10), walk-forward by year.

For each session, features known at the close of bar `minute` (9:35 or 10:00 for minute 4 / 29)
and the label = sign of the return from the next bar's open to the 10:55 open. For each test
year Y, a model is trained only on sessions before Y (expanding window) and predicts Y: every
prediction is out of sample. The MLDirection strategy reads those predictions and trades when
P(up) is beyond 0.5 +/- margin, stop stop_atr daily ATRs away, flat at the 10:55 flatten.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from .base import Signal
from .library import REGISTRY, _Base, _ok

PREDICTIONS: dict[tuple[str, int, str], pd.Series] = {}      # (data key, minute, model) -> P(up) by day

FEATURES = ["gap_adj", "ret_open", "range_atr", "cum_relvol", "rsi14", "roc5", "vwap_dist", "pm_range",
            "ret_d1", "ret_d5", "sma_d20_dist", "vix_prev", "rv_d20", "atr_pct", "dow", "tom", "pre_holiday",
            "nr4", "nr7", "first_dir"]


def design(feats: pd.DataFrame, ctx: pd.DataFrame, minute: int) -> pd.DataFrame:
    r = feats[feats["minute"] == minute].set_index("day")
    entry = feats[feats["minute"] == minute + 1].set_index("day")["open"]
    exit_ = feats[feats["minute"] == 85].set_index("day")["open"]          # the 10:55 bar's open
    c = ctx.reindex(r.index)
    X = pd.DataFrame(index=r.index)
    X["gap_adj"] = c["gap_adj"]
    X["ret_open"] = r["ret_open"]
    X["range_atr"] = (r["sess_high"] - r["sess_low"]) / c["atr_d14"]
    X["cum_relvol"] = r["cum_relvol"].replace([np.inf, -np.inf], np.nan)
    X["rsi14"], X["roc5"] = r["rsi14"], r["roc5"]
    X["vwap_dist"] = (r["close"] - r["vwap"]) / r["atr14"]
    X["pm_range"] = (c["pm_high"] - c["pm_low"]) / c["prev_close"]
    for k in ("ret_d1", "ret_d5", "sma_d20_dist", "rv_d20"):
        X[k] = c[k]
    X["vix_prev"] = c["vix_prev"] if "vix_prev" in c else np.nan
    X["atr_pct"] = c["atr_d14"] / c["prev_close"]
    X["dow"] = c["dow"]
    for k in ("tom", "pre_holiday", "nr4", "nr7"):
        X[k] = c[k].astype(float)
    X["first_dir"] = np.sign(r["ret_open"])
    X["label"] = (exit_.reindex(X.index) / entry.reindex(X.index) - 1)
    return X


def _model(kind: str):
    if kind == "logit":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.impute import SimpleImputer
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.1, max_iter=500))
    import lightgbm as lgb
    return lgb.LGBMClassifier(n_estimators=150, learning_rate=0.03, max_depth=3, num_leaves=7, min_child_samples=60,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0, verbose=-1)


def walkforward_predictions(X: pd.DataFrame, kind: str, first_test_year: int) -> pd.Series:
    out = []
    years = sorted({d.year for d in X.index})
    for y in years:
        if y < first_test_year:
            continue
        tr = X[[d.year < y for d in X.index]].dropna(subset=["label"])
        te = X[[d.year == y for d in X.index]]
        if len(tr) < 250 or te.empty:
            continue
        m = _model(kind)
        m.fit(tr[FEATURES], (tr["label"] > 0).astype(int))
        out.append(pd.Series(m.predict_proba(te[FEATURES])[:, 1], index=te.index))
    return pd.concat(out) if out else pd.Series(dtype=float)


class MLDirection(_Base):
    name = "ml_direction"

    @classmethod
    def defaults(cls):
        return {"key": "", "minute": 4, "model": "logit", "margin": 0.03, "stop_atr": 0.5}

    def on_session_start(self, day: dt.date, ctx: pd.Series) -> None:
        super().on_session_start(day, ctx)
        pr = PREDICTIONS.get((self.params["key"], self.params["minute"], self.params["model"]))
        self.p_up = float(pr.get(day, math.nan)) if pr is not None else math.nan

    def on_bar_close(self, row, position):
        p = self.params
        if position is not None or self.entries or row["minute"] != p["minute"] or not _ok(self.p_up):
            return None
        a = self.ctx.get("atr_d14")
        if not _ok(a):
            return None
        side = 1 if self.p_up > 0.5 + p["margin"] else -1 if self.p_up < 0.5 - p["margin"] else 0
        if not side:
            return None
        self.entries += 1
        return Signal(side, row["close"] - side * p["stop_atr"] * a, None, None,
                      reason=f"model P(up) {self.p_up:.3f}", evidence={"p_up": self.p_up})


REGISTRY[MLDirection.name] = MLDirection
