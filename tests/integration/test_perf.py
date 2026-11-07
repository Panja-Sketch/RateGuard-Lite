"""A6 performance targets. Run with: pytest -m slow"""

import time

import pytest

from rateguard_lite.samples.generator import build_scenario
from rateguard_lite.samples.runner import check, run_scenario

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("tables,limit", [(120, 30), (220, 60)])
def test_performance(tables, limit, gen_dir, tmp_path):
    sc = build_scenario("clean_pass", gen_dir / f"perf{tables}", tables=tables)
    t = time.perf_counter()
    o = run_scenario(sc, tmp_path, write_reports=True)
    elapsed = time.perf_counter() - t
    assert check(sc, o) == []
    assert elapsed <= limit, f"{tables} tables took {elapsed:.1f}s (limit {limit}s)"
