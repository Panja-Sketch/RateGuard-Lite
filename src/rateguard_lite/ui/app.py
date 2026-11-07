"""RateGuard-Lite local UI. Started only through `rgl ui` (127.0.0.1, headless, no telemetry)."""

from __future__ import annotations

import atexit
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import streamlit as st

from rateguard_lite import __version__
from rateguard_lite.config import config_dir, data_dir, load_config
from rateguard_lite.errors import RGLError
from rateguard_lite.guard.netguard import install_socket_guard
from rateguard_lite.learning.review import apply_decision, describe
from rateguard_lite.learning.store import LearningStore
from rateguard_lite.model.findings import GATE_TEXT, Verdict
from rateguard_lite.pipeline import RunOptions, run_reconciliation
from rateguard_lite.snapshots.store import SnapshotStore

install_socket_guard()
st.set_page_config(page_title="RateGuard-Lite", page_icon="🛡️", layout="wide")

CSS = """
<style>
:root{--ink:#0b1f3a;--accent:#0e9aa7;--line:#dfe5ee}
.block-container{padding-top:1.2rem;max-width:1250px}
.rg-top{background:#0b1f3a;color:#fff;border-radius:12px;padding:14px 22px;display:flex;align-items:center;gap:14px;margin-bottom:18px}
.rg-top b{font-size:18px;letter-spacing:.2px}.rg-top span{color:#7ee0ea}.rg-top small{margin-left:auto;opacity:.7}
.rg-hero{border-radius:12px;padding:18px 22px;border-left:8px solid #667085;background:rgba(127,127,127,.08);margin:6px 0 14px}
.rg-hero.PASS{border-left-color:#067647}.rg-hero.FAIL,.rg-hero.BLOCKED{border-left-color:#b42318}
.rg-hero.PASS_WITH_WARNINGS,.rg-hero.PASS_WITH_LIMITATIONS{border-left-color:#b54708}.rg-hero.NEEDS_REVIEW{border-left-color:#5b3fd1}
.rg-hero h2{margin:0;font-size:22px}.rg-hero p{margin:2px 0 0;opacity:.8}
[data-testid="stMetricValue"]{font-size:1.7rem}
.stTabs [data-baseweb="tab"]{font-weight:600}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---- per-session temp dir; stale sweep at startup (spec 11.2) -------------------------------------------
def _sweep_stale() -> None:
    root = Path(tempfile.gettempdir())
    for d in root.glob("rgl_ui_*"):
        try:
            if time.time() - d.stat().st_mtime > 6 * 3600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


@st.cache_resource
def _startup() -> bool:
    _sweep_stale()
    return True


_startup()


def _session_dir() -> Path:
    if "tmp" not in st.session_state:
        d = Path(tempfile.mkdtemp(prefix="rgl_ui_"))
        st.session_state["tmp"] = str(d)
        atexit.register(shutil.rmtree, d, True)
    return Path(st.session_state["tmp"])


def _save_upload(up: Any, sub: str) -> Path:
    d = _session_dir() / sub
    d.mkdir(parents=True, exist_ok=True)
    p = d / Path(up.name).name
    p.write_bytes(up.getbuffer())
    return p


st.markdown(f'<div class="rg-top"><div style="font-size:26px">🛡️</div><div><b>RateGuard<span>·Lite</span></b><br>'
            f'<small style="margin:0;opacity:.75">Rate book verification · local &amp; offline</small></div><small>v{__version__}</small></div>', unsafe_allow_html=True)

tab_run, tab_review, tab_hist, tab_set = st.tabs(["Run", "Review & Learn", "History & Stats", "Settings"])

# ============================================================ RUN
with tab_run:
    c1, c2 = st.columns(2)
    with c1:
        up_appr = st.file_uploader("1 · Approved workbook", type=["xlsx", "xlsm", "zip"], help="Multi-tab workbook or a .zip of workbooks (whole tables).")
        up_jira = st.file_uploader("2 · Jira story", type=["txt", "md", "html", "htm"])
    with c2:
        up_exp = st.file_uploader("3 · Guidewire export (after implementation)", type=["xlsx"])
        up_base = st.file_uploader("4 · Baseline export (optional)", type=["xlsx"], help="The prior edition. If you skip it, the tool uses its own snapshot history when available.")
    o1, o2, o3 = st.columns([1, 1, 2])
    lob = o1.text_input("LOB override", "", placeholder="e.g. BOP")
    state = o2.text_input("State override", "", placeholder="e.g. OR", max_chars=2)
    accept = o3.checkbox("If it passes, accept this export as the baseline for the next run", value=False)
    go = st.button("Run reconciliation", type="primary", disabled=not (up_appr and up_jira and up_exp))
    if go and up_appr and up_jira and up_exp:
        out = _session_dir() / f"out_{int(time.time())}"
        with st.status("Running...", expanded=True) as status:
            try:
                res = run_reconciliation(
                    RunOptions(_save_upload(up_appr, "approved"), _save_upload(up_jira, "jira"), _save_upload(up_exp, "export"), out,
                               _save_upload(up_base, "baseline") if up_base else None, lob or None, state or None, accept_snapshot=accept),
                    progress=lambda m: status.write(m))
                st.session_state["res"] = res
                st.session_state["out"] = str(out)
                status.update(label=f"Done: {res.verdict.value}", state="complete")
            except RGLError as exc:
                status.update(label="Could not continue", state="error")
                st.error(f"**What:** {exc.what}\n\n**Where:** {exc.where or '-'}\n\n**Next:** {exc.next_step or '-'}")
            except Exception as exc:  # noqa: BLE001
                status.update(label="Internal error", state="error")
                st.error(f"Unexpected error ({type(exc).__name__}). Details are in the run log; no input data is logged.")

    res = st.session_state.get("res")
    if res is not None:
        s = res.summary
        v: Verdict = res.verdict
        st.markdown(f'<div class="rg-hero {v.value}"><h2>{v.value.replace("_", " ")} — {s["ticket"] or "Rate book"}</h2><p>{GATE_TEXT[v]}</p></div>', unsafe_allow_html=True)
        m = st.columns(6)
        m[0].metric("Tables analyzed", s["tables_analyzed"])
        m[1].metric("Matched", s["tables_matched"])
        m[2].metric("Discrepancies", s["tables_with_discrepancies"])
        m[3].metric("Scope violations", s["scope_violations"])
        m[4].metric("Cells compared", f"{s['cells_compared']:,}")
        m[5].metric("Rounded matches", s["rounded_matches"])
        st.caption(f"Rate book {s['rate_book'] or '-'} · edition {s['export_edition'] or '-'} · filing {s['filing_effective_date'] or '-'} · baseline: {s['baseline']}")
        rows = [{"Level": f.level, "Rule": f.rule_id, "Table": f.table_name or f.table_id, "Key": f.key, "Column": f.column, "Approved": f.approved_value,
                 "Export": f.export_value, "Detail": f.message, "Suggested fix": f.suggested_fix} for f in res.findings if f.level != "INFO"]
        levels = sorted({r["Level"] for r in rows})
        pick = st.multiselect("Show levels", levels, default=levels)
        if rows:
            st.dataframe([r for r in rows if r["Level"] in pick], width="stretch", hide_index=True)
        else:
            st.success("No findings. Every approved value landed correctly.")
        out = Path(st.session_state["out"])
        d = st.columns(5)
        for col, (label, name, mime) in zip(d, [("HTML report", "reconciliation_report.html", "text/html"), ("Excel report", "reconciliation_report.xlsx", "application/vnd.ms-excel"),
                                                ("JSON", "reconciliation_report.json", "application/json"), ("Jira comment", "jira_comment.txt", "text/plain"),
                                                ("QA scenarios", "test_scenarios.xlsx", "application/vnd.ms-excel")], strict=True):
            f = out / name
            if f.exists():
                col.download_button(label, f.read_bytes(), file_name=name, mime=mime, width="stretch")
        if res.snapshot_id and v in (Verdict.PASS, Verdict.PASS_WITH_WARNINGS, Verdict.PASS_WITH_LIMITATIONS):
            if st.button("Accept this export as the baseline for the next run"):
                sn = SnapshotStore()
                try:
                    sn.accept(res.snapshot_id)
                    st.success("Snapshot accepted.")
                finally:
                    sn.close()

# ============================================================ REVIEW & LEARN
with tab_review:
    res = st.session_state.get("res")
    store = LearningStore()
    try:
        st.subheader("Proposals from the last run")
        props = [p for p in (res.proposals if res else []) if p.get("kind") in ("TABLE_ALIAS", "HIDDEN_SHEET", "LAYOUT")]
        if not props:
            st.info("Nothing to review. All mappings are confirmed.")
        for i, p in enumerate(props):
            with st.container(border=True):
                st.write(describe(p))
                choice = None
                if p.get("status") == "NEEDS_MAPPING" and p.get("candidates"):
                    labels = [f"{c['names'][0]}  (score {c['score']})" for c in p["candidates"]]
                    sel = st.radio("Matching export table", ["None of these"] + labels, key=f"c{i}")
                    choice = labels.index(sel) if sel in labels else None
                b1, b2 = st.columns(2)
                if b1.button("Confirm", key=f"a{i}", disabled=(p.get("status") == "NEEDS_MAPPING" and choice is None) or p.get("kind") == "LAYOUT"):
                    st.success(apply_decision(store, p, "accept", choice))
                if b2.button("Reject", key=f"r{i}"):
                    st.info(apply_decision(store, p, "reject"))
        st.caption("Learning only changes how files are read and mapped. It never changes comparison results or verdicts.")
        st.subheader("Learned items")
        items = store.list()
        st.dataframe([{"ID": r["id"], "Kind": r["kind"], "LOB": r["scope_lob"], "Table": r["scope_table"], "Key": r["key"], "Value": r["value_json"], "Status": r["status"],
                       "By": r["confirmed_by"] or r["created_by"], "v": r["version"]} for r in items], width="stretch", hide_index=True)
        rid = st.number_input("Retire item ID", min_value=0, step=1)
        if st.button("Retire") and rid:
            store.retire(int(rid))
            st.success(f"Item {int(rid)} retired. Reload to refresh.")
    finally:
        store.close()

# ============================================================ HISTORY
with tab_hist:
    store = LearningStore()
    snaps = SnapshotStore()
    try:
        stt = store.stats()
        k = st.columns(4)
        k[0].metric("Runs recorded", stt["runs"])
        k[1].metric("Auto-mapped tables", "n/a" if stt["auto_mapped_rate"] is None else f"{stt['auto_mapped_rate'] * 100:.0f}%")
        k[2].metric("Suggestion acceptance", "n/a" if stt["suggestion_acceptance_rate"] is None else f"{stt['suggestion_acceptance_rate'] * 100:.0f}%")
        k[3].metric("Median manual mappings / run", stt["median_manual_mappings_per_run"])
        if stt["manual_mappings_over_time"]:
            st.line_chart(stt["manual_mappings_over_time"], height=160)
            st.caption("Manual mappings per run. A falling line means the tool is learning your naming.")
        st.subheader("Snapshot history")
        st.dataframe([{"Snapshot": s.id, "Rate book": s.book_key, "Edition": s.edition, "Effective": s.effective_date, "Status": s.status, "Verdict": s.verdict,
                       "Tables": s.tables, "Taken": s.taken_at[:16], "Accepted by": s.accepted_by} for s in snaps.list()], width="stretch", hide_index=True)
        runs = list(store.db.execute("SELECT id, started_at, verdict, tool_version FROM run ORDER BY started_at DESC LIMIT 50"))
        st.subheader("Recent runs")
        st.dataframe([dict(r) for r in runs], width="stretch", hide_index=True)
    finally:
        store.close()
        snaps.close()

# ============================================================ SETTINGS
with tab_set:
    cfg = load_config()
    st.write(f"**Version** {__version__}  ·  **Data folder** `{data_dir()}`  ·  **Config folder** `{config_dir()}`")
    st.write("**Network**: blocked. The tool never makes outbound connections; the UI is bound to 127.0.0.1.")
    st.json(cfg.model_dump())
    st.caption("Edit config/default.yaml (or a profile) to change tolerances and limits. Changes apply on the next run.")
    if st.button("Delete all snapshots"):
        sn = SnapshotStore()
        n = sn.purge(all_=True)
        sn.close()
        st.warning(f"Removed {n} snapshot(s).")
