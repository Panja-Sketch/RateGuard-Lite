import socket
import zipfile
from pathlib import Path

import pytest

from rateguard_lite.config import LimitsCfg
from rateguard_lite.errors import NetworkBlockedError, UnsafeInputError
from rateguard_lite.guard.netguard import install_socket_guard
from rateguard_lite.guard.safety import safety_gate_text, safety_gate_workbook, safety_gate_zip

LIM = LimitsCfg()
SRC = Path(__file__).resolve().parents[2] / "src" / "rateguard_lite"


def test_external_connection_blocked():
    install_socket_guard()
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("93.184.216.34", 80), timeout=1)
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("example.com", 80)


def test_loopback_allowed():
    install_socket_guard()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.create_connection(("127.0.0.1", srv.getsockname()[1]), timeout=2)
    c.close()
    srv.close()


def test_wrong_extension(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("x")
    with pytest.raises(UnsafeInputError):
        safety_gate_workbook(p, LIM)


def test_bad_magic(tmp_path):
    p = tmp_path / "a.xlsx"
    p.write_bytes(b"not a zip at all")
    with pytest.raises(UnsafeInputError):
        safety_gate_workbook(p, LIM)


def test_encrypted_container(tmp_path):
    p = tmp_path / "a.xlsx"
    p.write_bytes(b"\xd0\xcf\x11\xe0" + b"\0" * 100)
    with pytest.raises(UnsafeInputError, match="encrypted"):
        safety_gate_workbook(p, LIM)


def test_zip_traversal(tmp_path):
    p = tmp_path / "a.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("../x.xlsx", b"1")
    with pytest.raises(UnsafeInputError, match="unsafe path"):
        safety_gate_zip(p, LIM)


def test_zip_bomb_ratio(tmp_path):
    p = tmp_path / "a.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.xlsx", b"\0" * 5_000_000)
    with pytest.raises(UnsafeInputError, match="compression ratio"):
        safety_gate_zip(p, LIM)


def test_size_limit(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"x" * 2_000_000)
    with pytest.raises(UnsafeInputError):
        safety_gate_text(p, LimitsCfg(max_file_mb=1))


def test_no_dangerous_calls_in_source():
    """Spec 11.4: no eval/exec/pickle/os.system; subprocess only in the CLI's `ui` command."""
    import re

    banned = [r"(?<![\w.])eval\(", r"(?<![\w.])exec\(", r"\bpickle\b", r"os\.system", r"shell\s*=\s*True"]
    for f in SRC.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for pat in banned:
            for line in text.splitlines():
                code = line.split("#")[0]
                if re.search(pat, code):
                    pytest.fail(f"{f.name}: found {pat!r}: {line.strip()}")
        if "import subprocess" in text and f.name != "cli.py":
            pytest.fail(f"subprocess used outside cli.py: {f.name}")
