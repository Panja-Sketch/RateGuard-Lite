import os
import tempfile
from pathlib import Path

import pytest

from rateguard_lite.guard.netguard import install_socket_guard

install_socket_guard()
_DATA = tempfile.mkdtemp(prefix="rgl_test_data_")
os.environ["RGL_DATA_DIR"] = _DATA


@pytest.fixture(scope="session")
def gen_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("scenarios")


@pytest.fixture(scope="session")
def scenario(gen_dir):
    from rateguard_lite.samples.generator import build_scenario

    cache: dict = {}

    def get(name: str, tables: int = 30):
        key = (name, tables)
        if key not in cache:
            cache[key] = build_scenario(name, gen_dir / f"t{tables}", tables=tables)
        return cache[key]

    return get
