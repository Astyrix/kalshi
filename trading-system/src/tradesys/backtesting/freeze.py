"""Freezing a strategy before it may touch the out-of-sample period.

A frozen spec records the strategy name and parameters, the full configuration fingerprint,
the git commit of the code, and the time. The out-of-sample runner refuses any spec that is
not frozen, and records every OOS run in an append-only log: running the same frozen spec
twice is fine, but a spec frozen AFTER an earlier OOS run of the same strategy family is
flagged as contaminated (the holdout has been seen).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from ..config import ROOT, Config


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "src", "config"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return out.stdout.strip() + ("+dirty" if dirty else "")
    except Exception:
        return "unknown"


def freeze(cfg: Config, strategy: str, params: dict[str, Any], note: str = "") -> Path:
    spec = {"strategy": strategy, "params": params, "config_fingerprint": cfg.fingerprint(),
            "config": cfg.model_dump(mode="json"), "code": _git_commit(),
            "frozen_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "note": note}
    if spec["code"].endswith("+dirty") or spec["code"] == "unknown":
        raise RuntimeError("commit the code first: a frozen spec must point at a clean git commit")
    blob = json.dumps({k: spec[k] for k in ("strategy", "params", "config_fingerprint")}, sort_keys=True)
    sid = hashlib.sha256(blob.encode()).hexdigest()[:12]
    d = ROOT / cfg.validation.frozen_dir
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{strategy}-{sid}.json"
    if p.exists():
        return p
    p.write_text(json.dumps(spec, indent=1))
    return p


def record_oos_run(cfg: Config, spec_path: Path, summary: dict[str, Any]) -> dict[str, Any]:
    log = ROOT / cfg.validation.frozen_dir / "oos_runs.jsonl"
    spec = json.loads(Path(spec_path).read_text())
    prior = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    contaminated = any(r["strategy"] == spec["strategy"] and r["spec"] != Path(spec_path).name
                       and r["run_utc"] < spec["frozen_utc"] for r in prior)
    rec = {"spec": Path(spec_path).name, "strategy": spec["strategy"], "run_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
           "contaminated": contaminated, "summary": summary}
    with log.open("a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    return rec
