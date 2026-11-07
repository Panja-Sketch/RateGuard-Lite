from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[2] / "src" / "rateguard_lite" / "ui" / "app.py"


def test_ui_boots_and_renders_all_tabs():
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Run", "Review & Learn", "History & Stats", "Settings"]
    run_btn = [b for b in at.button if b.label == "Run reconciliation"]
    assert run_btn and run_btn[0].disabled          # cannot run without the three required files


def test_streamlit_config_is_loopback_only():
    cfg = (APP.parents[3] / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert 'address = "127.0.0.1"' in cfg and "gatherUsageStats = false" in cfg and "headless = true" in cfg
