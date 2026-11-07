"""Run a generated scenario end-to-end and compare with its expectation (used by selftest and tests)."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import load_config
from ..errors import RGLError
from ..ingest.gw_export import ingest_export
from ..pipeline import RunOptions, RunResult, run_reconciliation
from ..snapshots.store import SnapshotStore
from .generator import Scenario


@dataclass
class Outcome:
    verdict: str
    exit_code: int
    rules: set[str] = field(default_factory=set)
    result: RunResult | None = None
    error: str = ""


@contextmanager
def data_dir_env(path: Path) -> Iterator[None]:
    """Point the data folder AND the team mappings file at a scratch location (a self-test must never touch the real ones)."""
    old = {k: os.environ.get(k) for k in ("RGL_DATA_DIR", "RGL_TEAM_MAPPINGS")}
    os.environ["RGL_DATA_DIR"] = str(path)
    os.environ["RGL_TEAM_MAPPINGS"] = str(path / "team_mappings.yaml")
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def seed_snapshot(baseline: Path) -> None:
    cfg = load_config("snapshots")
    ing = ingest_export(baseline, cfg, "EXPORT", "BOP")
    st = SnapshotStore()
    try:
        st.save("R-SEED", ing.book, baseline.name, ing.input_files[0]["sha256"], "PASS", "VERIFIED", "selftest")
    finally:
        st.close()


def run_scenario(sc: Scenario, work: Path, write_reports: bool = False, **kw: Any) -> Outcome:
    out = work / sc.name
    if out.exists():
        shutil.rmtree(out)
    with data_dir_env(work / "data" / sc.name):
        if sc.seed_snapshot:
            seed_snapshot(sc.baseline)  # type: ignore[arg-type]
        try:
            res = run_reconciliation(RunOptions(
                approved=sc.approved, jira=sc.jira, export=sc.export, out=out, profile=sc.profile,
                baseline=sc.baseline if sc.use_baseline_flag else None, write_reports=write_reports, user="selftest", **kw))
        except RGLError as exc:
            return Outcome("BLOCKED", exc.exit_code, set(), None, exc.render())
    return Outcome(res.verdict.value, res.exit_code, {f.rule_id for f in res.findings}, res)


def check(sc: Scenario, o: Outcome) -> list[str]:
    problems = []
    if o.verdict != sc.expected_verdict:
        problems.append(f"verdict {o.verdict} != expected {sc.expected_verdict}")
    if o.exit_code != sc.expected_exit:
        problems.append(f"exit code {o.exit_code} != expected {sc.expected_exit}")
    missing = sc.expected_rules - o.rules
    if missing:
        problems.append(f"missing expected finding(s): {sorted(missing)}")
    return problems
