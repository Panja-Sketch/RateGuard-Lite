import json
import os
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from rateguard_lite.cli import app
from rateguard_lite.errors import RGLError
from rateguard_lite.learning.review import apply_decision
from rateguard_lite.learning.store import LearningStore
from rateguard_lite.pipeline import RunOptions, run_reconciliation
from rateguard_lite.samples import generator as g
from rateguard_lite.samples.runner import data_dir_env, run_scenario
from rateguard_lite.snapshots.store import SnapshotStore

VOLATILE = {"run_id", "started_at", "user"}


def report_json(res) -> str:
    d = res.to_report().model_dump(mode="json")
    for k in VOLATILE:
        d["run"].pop(k, None)
    return json.dumps(d, sort_keys=True)


def test_determinism_three_runs_identical(scenario, tmp_path):                 # A5
    sc = scenario("factor_mismatch")
    outs = [report_json(run_scenario(sc, tmp_path / str(i)).result) for i in range(3)]
    assert outs[0] == outs[1] == outs[2]


def test_row_order_invariance(gen_dir, tmp_path):                              # A4
    """Same data, different row shuffles in the export -> identical report."""
    sc = g.build_scenario("factor_mismatch", gen_dir / "shuf", tables=25)
    old = g.build_world(25, 7)
    new = g.apply_change(old)
    new[next(i for i, t in enumerate(new) if t.code == "BOP_BLD_NumStories_Ext")].rows[2][-1] = Decimal("1.0250")
    alt = sc.dir / "export_alt.xlsx"
    g.write_export(alt, new, g.NEW_EDITION, g.NEW_EFF, rng_seed=99)
    a = run_scenario(sc, tmp_path / "a").result
    sc2 = type(sc)(**{**sc.__dict__, "export": alt, "name": "alt"})
    b = run_scenario(sc2, tmp_path / "b").result
    strip = lambda r: sorted((f.rule_id, f.table_id, f.key, f.column, f.approved_value, f.export_value) for f in r.findings)  # noqa: E731
    assert strip(a) == strip(b) and a.verdict == b.verdict


def test_learning_resolves_mapping_but_never_turns_fail_into_pass(gen_dir, tmp_path):   # A9
    work = tmp_path / "w"
    sc = g.build_scenario("unmappable_table_blocked", gen_dir / "learn", tables=25)
    with data_dir_env(work / "data"):
        r1 = run_reconciliation(RunOptions(sc.approved, sc.jira, sc.export, work / "o1", sc.baseline, user="t"))
        assert r1.verdict.value == "BLOCKED"
        needs = [p for p in r1.proposals if p.get("status") == "NEEDS_MAPPING"]
        assert needs and needs[0]["candidates"]
        store = LearningStore()
        for p in needs:
            idx = next(i for i, c in enumerate(p["candidates"]) if any(p["approved"] in n or True for n in c["names"]))
            want = {"Ded Schedule Adj": "Deductible Factor", "Terr Rel Adj": "Territory Factor"}[p["approved"]]
            idx = next(i for i, c in enumerate(p["candidates"]) if want in c["names"])
            apply_decision(store, p, "accept", idx, "t")
        store.close()
        r2 = run_reconciliation(RunOptions(sc.approved, sc.jira, sc.export, work / "o2", sc.baseline, user="t"))
        assert not [f for f in r2.findings if f.level == "NEEDS_MAPPING"]            # 0 NEEDS_MAPPING after confirming once
        assert r2.verdict.value == "PASS"
        # Learning never changes comparison math: corrupt a value and the learned mapping still FAILs.
        sc3 = g.build_scenario("unmappable_table_blocked", gen_dir / "learn3", tables=25)
        old = g.build_world(25, 7)
        new = g.apply_change(old)
        next(t for t in new if t.code == "BOP_DeductibleFactor_Ext").rows[0][-1] = Decimal("9.999")
        g.write_export(sc3.export, new, g.NEW_EDITION, g.NEW_EFF, rng_seed=5)
        r3 = run_reconciliation(RunOptions(sc3.approved, sc3.jira, sc3.export, work / "o3", sc3.baseline, user="t"))
        assert r3.verdict.value == "FAIL" and any(f.rule_id in ("VALUE_MISMATCH", "ORDER_OF_MAGNITUDE") for f in r3.findings)


def test_confirmed_goes_to_team_file_proposed_stays_local(tmp_path, monkeypatch):          # D-024
    team = tmp_path / "team_mappings.yaml"
    monkeypatch.setenv("RGL_TEAM_MAPPINGS", str(team))
    st = LearningStore(tmp_path / "l.db")
    a = st.add("TABLE_ALIAS", "FOO BAR", {"canonical_id": "BOP:1"}, scope_lob="BOP", status="CONFIRMED", user="u1")
    assert isinstance(a, str) and a.startswith("T-")
    assert st.list(status="CONFIRMED") == []                                                  # SQLite never holds CONFIRMED
    entry = yaml.safe_load(team.read_text(encoding="utf-8"))["items"][0]
    assert entry["confirmed_by"] == "u1" and entry["confirmed_on"] == date.today().isoformat() and entry["value"] == {"canonical_id": "BOP:1"}
    assert st.context("BOP").alias_for("BOP", "FOO BAR") == "BOP:1"
    st.add("TABLE_ALIAS", "FOO BAR", {"canonical_id": "BOP:2"}, scope_lob="BOP", status="CONFIRMED", user="u2")   # same key: replaced, not duplicated
    assert len(yaml.safe_load(team.read_text(encoding="utf-8"))["items"]) == 1
    assert st.context("BOP").alias_for("BOP", "FOO BAR") == "BOP:2"
    # proposed -> local only, then promoted by an explicit confirm
    pid = st.add("TABLE_ALIAS", "BAZ", {"canonical_id": "BOP:9"}, scope_lob="BOP")
    assert isinstance(pid, int) and st.context("BOP").table_aliases.get(("BOP", "BAZ")) is None
    tid = st.confirm(pid, "u3")
    assert st.get(pid)["status"] == "PROMOTED" and st.context("BOP").alias_for("BOP", "BAZ") == "BOP:9"
    assert st.retire(tid) and st.context("BOP").alias_for("BOP", "BAZ") is None              # removal from the team file
    # sharing: a second machine imports as PROPOSED and must confirm
    out = tmp_path / "shared.yaml"
    assert st.export_yaml(out) == 1
    monkeypatch.setenv("RGL_TEAM_MAPPINGS", str(tmp_path / "other_team.yaml"))
    other = LearningStore(tmp_path / "l2.db")
    other.import_yaml(out)
    assert other.list(status="PROPOSED") and other.confirmed_items() == []
    assert other.context("BOP").table_aliases == {}
    try:
        st.add("MAGIC_TOLERANCE", "x", {})                      # only allow-listed kinds can be learned
        raise AssertionError("should have been rejected")
    except Exception as exc:
        assert "not a learnable" in str(exc)


def test_bad_team_file_is_a_clear_error(tmp_path, monkeypatch):
    bad = tmp_path / "t.yaml"
    bad.write_text("rateguard_lite_mappings: 1\nitems:\n  - {kind: MAGIC, key: x, value: {}}\n", encoding="utf-8")
    monkeypatch.setenv("RGL_TEAM_MAPPINGS", str(bad))
    with pytest.raises(RGLError, match="Invalid entry"):
        LearningStore(tmp_path / "l.db").context("BOP")


def test_false_positive_is_only_a_hint(scenario, tmp_path):
    sc = scenario("factor_mismatch")
    with data_dir_env(tmp_path / "d"):
        r1 = run_reconciliation(RunOptions(sc.approved, sc.jira, sc.export, tmp_path / "o1", sc.baseline, user="t"))
        f = r1.findings[0]
        st = LearningStore()
        st.record_disposition(r1.run_id, f.rule_id, f.table_id, f.key, f.column, "FALSE_POSITIVE", "approved file is stale", "qa")
        st.close()
        r2 = run_reconciliation(RunOptions(sc.approved, sc.jira, sc.export, tmp_path / "o2", sc.baseline, user="t"))
        assert r2.verdict.value == "FAIL"                        # still counts
        assert "false positive" in r2.findings[0].details["hint"]


def test_snapshot_flow_second_run_uses_baseline(gen_dir, tmp_path):
    """Opt-in Snapshot History (--profile snapshots): a taken snapshot becomes the next run's baseline."""
    sc = g.build_scenario("snapshot_first_run_limitations", gen_dir / "snapflow", tables=25)
    with data_dir_env(tmp_path / "d"):
        # run 1 on the prior edition export accepted as snapshot
        old = g.build_world(25, 7)
        prior = tmp_path / "prior.xlsx"
        g.write_export(prior, old, g.OLD_EDITION, g.OLD_EFF, rng_seed=2)
        r = CliRunner().invoke(app, ["snapshot", "take", "--export", str(prior)])
        assert r.exit_code == 0, r.output
        r2 = run_reconciliation(RunOptions(sc.approved, sc.jira, sc.export, tmp_path / "o", None, user="t", profile="snapshots"))
        assert r2.baseline_info["mode"] == "snapshot" and r2.verdict.value == "PASS"
        assert "snapshot" in r2.summary["baseline"] and r2.summary["scope_check_performed"]
        # default profile: Snapshot History is off, so the snapshot is ignored and nothing new is saved
        r3 = run_reconciliation(RunOptions(sc.approved, sc.jira, sc.export, tmp_path / "o3", None, user="t"))
        assert r3.baseline_info["mode"] == "none" and r3.snapshot_id is None and not r3.summary["scope_check_performed"]


def test_cli_exit_codes_and_overwrite(scenario, tmp_path):
    sc = scenario("clean_pass")
    runner = CliRunner()
    base = ["run", "--approved", str(sc.approved), "--jira", str(sc.jira), "--export", str(sc.export), "--baseline", str(sc.baseline)]
    ok = runner.invoke(app, base + ["--out", str(tmp_path / "o")])
    assert ok.exit_code == 0 and "PASS" in ok.output
    again = runner.invoke(app, base + ["--out", str(tmp_path / "o")])
    assert again.exit_code == 3                                           # will not clobber a previous run
    assert runner.invoke(app, base + ["--out", str(tmp_path / "o"), "--overwrite"]).exit_code == 0
    bad = runner.invoke(app, ["run", "--approved", str(tmp_path / "nope.xlsx"), "--jira", str(sc.jira), "--export", str(sc.export), "--out", str(tmp_path / "o2")])
    assert bad.exit_code == 3 and "Traceback" not in bad.output


def test_drift_mode(gen_dir, tmp_path):
    left, right = g.build_drift_pair(gen_dir / "drift", tables=20)
    r = CliRunner().invoke(app, ["drift", "--left", str(left), "--right", str(right), "--out", str(tmp_path / "d")])
    assert r.exit_code == 1
    data = json.loads((tmp_path / "d" / "drift_report.json").read_text())
    assert data["summary"]["differing"] >= 2 and data["summary"]["only_left"] == 1 and data["summary"]["only_right"] == 1
    assert any(d["type"] == "VALUE" for d in data["diffs"]) and any(d["type"] == "ROW_ONLY_LEFT" for d in data["diffs"])


def test_inspect_and_sample(gen_dir, tmp_path, scenario):
    sc = scenario("clean_pass")
    r = CliRunner().invoke(app, ["inspect", str(sc.export), "--max-sheets", "2"])
    assert r.exit_code == 0 and "Guidewire layout" in r.output
    r2 = CliRunner().invoke(app, ["sample", "--out", str(tmp_path / "s"), "--tables", "15"])
    assert r2.exit_code == 0 and (tmp_path / "s" / "clean_pass" / "approved.xlsx").exists()
    shutil.rmtree(tmp_path / "s")
    assert Path(os.environ["RGL_DATA_DIR"]).exists()


def test_review_cli_writes_team_file_with_user_and_date(gen_dir, tmp_path, monkeypatch):                # D-024 (4)
    team = tmp_path / "team.yaml"
    monkeypatch.setenv("RGL_TEAM_MAPPINGS", str(team))
    sc = g.build_scenario("unmappable_table_blocked", gen_dir / "review", tables=25)
    with data_dir_env(tmp_path / "d"):
        monkeypatch.setenv("RGL_TEAM_MAPPINGS", str(team))
        r1 = run_reconciliation(RunOptions(sc.approved, sc.jira, sc.export, tmp_path / "o1", sc.baseline, user="t", write_reports=True))
        assert r1.verdict.value == "BLOCKED"
        picks = "\n".join(str(1 + next(i for i, c in enumerate(p["candidates"]) if want in c["names"]))
                          for p in [q for q in r1.proposals if q.get("status") == "NEEDS_MAPPING"]
                          for want in [{"Ded Schedule Adj": "Deductible Factor", "Terr Rel Adj": "Territory Factor"}[p["approved"]]]) + "\n"
        res = CliRunner().invoke(app, ["map", "--run", str(tmp_path / "o1"), "--user", "qa.lead"], input=picks)
        assert res.exit_code == 0, res.output
        items = yaml.safe_load(team.read_text(encoding="utf-8"))["items"]
        assert items and all(i["confirmed_by"] == "qa.lead" and i["confirmed_on"] == date.today().isoformat() for i in items)
        assert LearningStore().list(status="CONFIRMED") == []
        r2 = run_reconciliation(RunOptions(sc.approved, sc.jira, sc.export, tmp_path / "o2", sc.baseline, user="t"))
        assert r2.verdict.value == "PASS" and any(u.get("source") == "team" for u in r2.learned_used)


def test_no_end_of_run_snapshot_prompt_and_accept_flag_still_works(scenario, tmp_path):                  # D-024 (1)
    sc = scenario("clean_pass")
    base = ["run", "--approved", str(sc.approved), "--jira", str(sc.jira), "--export", str(sc.export), "--baseline", str(sc.baseline)]
    with data_dir_env(tmp_path / "d"):
        res = CliRunner().invoke(app, base + ["--out", str(tmp_path / "o1")], input="y\n")
        assert res.exit_code == 0 and "Accept this export" not in res.output and "Snapshot" not in res.output
        res2 = CliRunner().invoke(app, base + ["--out", str(tmp_path / "o2"), "--accept-snapshot"])
        assert res2.exit_code == 0 and "accepted as the next baseline" in res2.output
        assert SnapshotStore().list()[0].status == "VERIFIED"
