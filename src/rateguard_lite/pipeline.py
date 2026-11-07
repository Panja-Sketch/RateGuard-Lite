"""End-to-end reconciliation pipeline: ingest -> map -> engines -> verdict -> reports."""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config, config_hash, load_config
from .engines.baseline import Delta, run_baseline
from .engines.checks import completeness_findings, integrity_findings
from .engines.common import make_finding, table_hash
from .engines.factors import FactorResult, compare_pair, emit_row_findings
from .engines.metadata import audit_metadata
from .engines.ranges import boundary_shifts, check_ranges
from .engines.scope import scope_findings, scope_matrix
from .errors import UnsafeInputError
from .guard.safety import safety_gate_text
from .ingest.approved import ingest_approved
from .ingest.gw_export import ingest_export
from .ingest.jira import JiraScope, parse_jira
from .ingest.result import IngestResult
from .learning.store import LearningStore, current_user
from .mapping.tables import MappingResult, harmonize_types, map_tables
from .model.findings import GATE_TEXT, Finding, Severity, Status, Verdict
from .model.report import InputRef, Report
from .snapshots.store import SnapshotStore, book_key
from .verdict import EXIT_BY_VERDICT, decide

log = logging.getLogger("rgl")
SCOPE_RULES = {"BYPASS_VIOLATION", "UNDECLARED_CHANGE", "MISSING_UPDATE", "REMOVE_NOT_APPLIED", "APPROVED_FILE_MISSING_TABLE",
               "TABLE_MISSING_IN_EXPORT"}
DECLARED = {"ADD", "MODIFY", "REPLACE"}


@dataclass
class RunOptions:
    approved: Path
    jira: Path
    export: Path
    out: Path
    baseline: Path | None = None
    lob: str | None = None
    state: str | None = None
    profile: str = "default"
    user: str | None = None
    overwrite: bool = False
    debug: bool = False
    accept_snapshot: bool = False
    write_reports: bool = True


@dataclass
class RunResult:
    run_id: str
    started_at: str
    verdict: Verdict
    exit_code: int
    findings: list[Finding]
    summary: dict[str, Any]
    jira: JiraScope
    mapping: MappingResult
    scope: list[dict[str, Any]]
    deltas: dict[str, Delta]
    factor_results: dict[str, FactorResult]
    inputs: list[InputRef]
    baseline_info: dict[str, Any]
    warnings: list[str]
    info: list[str]
    proposals: list[dict[str, Any]]
    learned_used: list[dict[str, Any]]
    tolerance_notes: list[str]
    timings: dict[str, float]
    cfg: Config
    cfg_hash: str
    approved_ing: IngestResult
    export_ing: IngestResult
    snapshot_id: str | None = None
    tests: Any = None
    out: Path | None = None
    user: str = ""
    learned_version: str = "0"
    lob: str = ""

    def to_report(self) -> Report:
        return Report(
            tool={"name": "RateGuard-Lite", "version": __version__, "config_hash": self.cfg_hash, "learned_store_version": self.learned_version},
            run={"run_id": self.run_id, "started_at": self.started_at, "user": self.user},
            inputs=self.inputs, verdict=self.verdict.value, gate=GATE_TEXT[self.verdict], exit_code=self.exit_code,
            summary=self.summary, jira=self.jira.model_dump(mode="json"), baseline=self.baseline_info,
            findings=self.findings, scope_matrix=self.scope,
            mapping=[{"table_id": p.id, "table": p.display, "approved": p.approved.display_name if p.approved else None,
                      "approved_sheet": f"{p.approved.source.file}!{p.approved.source.sheet}" if p.approved else None,
                      "export": p.export.display_name if p.export else None,
                      "export_sheet": p.export.metadata.get("sheet_name") if p.export else None,
                      "confidence": p.confidence, "score": round(p.score, 3), "reason": p.reason,
                      "jira_action": p.jira_action, "jira_confidence": p.jira_confidence}
                     for p in self.mapping.pairs if p.approved is not None or p.jira],
            warnings=self.warnings, info=self.info, learned_used=self.learned_used,
            tolerance_overrides=self.tolerance_notes,
            test_scenarios={"parameter_tests": len(getattr(self.tests, "parameter", [])), "transaction_tests": len(getattr(self.tests, "transaction", []))},
        )


def _setup_log(out: Path, debug: bool) -> logging.Handler:
    h = logging.FileHandler(out / "run.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    log.addHandler(h)
    return h


def _prepare_out(out: Path, overwrite: bool) -> None:
    if out.exists() and any(out.iterdir()):
        if not overwrite:
            raise UnsafeInputError("The output folder already contains files.", where=out.name,
                                   next_step="Choose a new --out folder, or pass --overwrite to replace the previous run.")
    out.mkdir(parents=True, exist_ok=True)


def run_reconciliation(opts: RunOptions, progress: Callable[[str], None] | None = None) -> RunResult:
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    started = datetime.now(UTC).isoformat(timespec="seconds")
    say = progress or (lambda _m: None)
    cfg = load_config(opts.profile)
    chash = config_hash(cfg)
    _prepare_out(opts.out, opts.overwrite)
    handler = _setup_log(opts.out, opts.debug)
    user = opts.user or current_user()
    lstore = LearningStore()
    snap = SnapshotStore() if cfg.snapshots.enabled else None
    try:
        log.info("run started; tool %s; config %s", __version__, chash)

        def stage(name: str, since: float) -> float:
            now_ = time.perf_counter()
            timings[name] = round(now_ - since, 2)
            log.info("stage %s done in %.2fs", name, timings[name])
            return now_

        s = time.perf_counter()
        say("Reading Jira story")
        safety_gate_text(opts.jira, cfg.limits)
        learned = lstore.context(opts.lob)
        jira = parse_jira(opts.jira, cfg, learned, opts.lob, opts.state)
        lob = (opts.lob or jira.lob or "").upper()
        learned = lstore.context(lob or None)
        s = stage("jira", s)

        say("Reading the Guidewire export")
        export_ing = ingest_export(opts.export, cfg, "EXPORT", lob or None, learned)
        if not lob:
            lob = export_ing.book.metadata.get("lob", "GW").upper()
        s = stage("export", s)
        log.info("export: %d tables, %d sheet issues", len(export_ing.book.tables), len(export_ing.issues))

        say("Reading the approved workbook")
        approved_ing = ingest_approved(opts.approved, cfg, lob, learned)
        s = stage("approved", s)
        log.info("approved: %d tables, %d sheet issues", len(approved_ing.book.tables), len(approved_ing.issues))

        # ---- baseline: explicit file, else Snapshot History
        baseline_book = None
        baseline_info: dict[str, Any] = {"mode": "none", "label": "none"}
        exp_meta = export_ing.book.metadata
        bkey = book_key(exp_meta)
        extra_inputs: list[InputRef] = []
        if opts.baseline:
            say("Reading the baseline export")
            b_ing = ingest_export(opts.baseline, cfg, "BASELINE", lob or None, learned)
            baseline_book = b_ing.book
            ed = baseline_book.metadata.get("edition", "?")
            baseline_info = {"mode": "provided", "label": f"provided ({opts.baseline.name}, edition {ed})", "edition": ed,
                             "file": opts.baseline.name, "effective_date": baseline_book.metadata.get("effective_date", "")}
            extra_inputs += [InputRef(role="baseline", name=f["name"], sha256=f["sha256"]) for f in b_ing.input_files]
        elif snap is not None:
            sel = snap.select_baseline(bkey, exp_meta.get("edition"), exp_meta.get("effective_date"))
            if sel is not None:
                hashes = {nid: table_hash(t) for nid, t in export_ing.book.tables.items()}
                baseline_book = snap.load_book(sel.id, hashes)
                baseline_info = {"mode": "snapshot", "snapshot_id": sel.id, "edition": sel.edition, "effective_date": sel.effective_date,
                                 "label": f"snapshot from run {sel.run_id}, accepted by {sel.accepted_by} on {(sel.accepted_at or '')[:10]}, edition {sel.edition}"}
        s = stage("baseline", s)

        # ---- mapping
        say("Mapping tables")
        mapping = map_tables(approved_ing.book, export_ing.book, baseline_book, jira, lob, cfg, learned)
        for p in mapping.pairs:
            if p.approved is not None and p.export is not None:
                p.approved = harmonize_types(p.approved, p.export, cfg)
        pairs = sorted(mapping.pairs, key=lambda p: (p.id, p.display))
        s = stage("mapping", s)

        findings: list[Finding] = []
        if not (opts.lob or jira.lob):
            findings.append(make_finding("LOB_UNRESOLVED", status=Status.NEEDS_REVIEW, message=f"No line of business in Jira or --lob; assumed '{lob}' from the export.",
                                         explanation="The line of business scopes table numbers and aliases.", suggested_fix="Pass --lob or add 'LOB: XXX' to the story."))
        # ---- Engine A + D(boundary)
        say("Comparing factors")
        fres: dict[str, FactorResult] = {}
        tol_notes: list[str] = []
        for p in pairs:
            if p.approved is None or p.export is None or p.jira_action == "BYPASS":
                continue
            fr = compare_pair(p, cfg)
            if fr.a_idx is not None:
                fr.findings += boundary_shifts(p, fr)
                emit_row_findings(fr, p)
            findings += fr.findings
            findings += completeness_findings(p.approved, p.export, p.id, p.display, cfg)
            if fr.abs_tolerance_note:
                tol_notes.append(fr.abs_tolerance_note)
            fres[p.id] = fr
        s = stage("factors", s)

        # ---- Engine B
        deltas: dict[str, Delta] = {}
        statuses: dict[str, str] = {}
        if baseline_book is not None:
            say("Comparing against the baseline")
            bf, deltas, statuses = run_baseline(pairs, fres, baseline_info["label"])
            findings += bf
        else:
            findings.append(make_finding(
                "NOT_EVALUATED_NO_BASELINE", status=Status.NOT_EVALUATED,
                message="No baseline available: all approved values were verified, but changes to other tables, bypassed tables and missing updates were not checked.",
                suggested_fix="Supply --baseline <prior export>, or accept this run as a snapshot (--accept-snapshot) so the next run has a baseline. "
                              "QA can also run `rgl snapshot take --export <file>` before development starts."))
        s = stage("baseline_delta", s)

        # ---- integrity (scope-limited on export side) and range checks
        say("Checking integrity and ranges")
        in_scope_export = set()
        for p in pairs:
            d = deltas.get(p.id)
            if p.export is not None and (p.approved is not None or p.jira_action in DECLARED or (d is not None and not d.empty and p.jira_action != "BYPASS")):
                in_scope_export.add(p.id)
        for p in pairs:
            if p.approved is not None:
                findings += integrity_findings(p.approved, p.id, p.display, "approved", cfg)
                findings += check_ranges(p.approved, p.id, p.display, "approved", learned)
            if p.export is not None and p.id in in_scope_export:
                findings += integrity_findings(p.export, p.id, p.display, "export", cfg)
                findings += check_ranges(p.export, p.id, p.display, "export", learned)
            for t in (p.approved, p.export):
                if t is not None:
                    for w in t.warnings:
                        findings.append(make_finding("TABLE_NOTE", severity=Severity.INFO, table_id=p.id, table_name=p.display, message=w,
                                                     explanation="Informational note from reading the table.", suggested_fix="No action needed."))
        s = stage("integrity", s)

        # ---- Engine C and E
        findings += scope_findings(mapping, jira, approved_ing, export_ing)
        has_changes = any(p.approved is not None for p in pairs) or any(p.jira_action in DECLARED for p in pairs)
        findings += audit_metadata(export_ing.book, approved_ing.book, baseline_book.metadata if baseline_book else None, jira, pairs, cfg, has_changes)
        for w in approved_ing.warnings + export_ing.warnings:
            findings.append(make_finding("INPUT_WARNING", severity=Severity.WARNING, message=w, explanation="Something unusual was found in an input file.",
                                         suggested_fix="Review the file; the tool did not follow or execute it."))
        for i in approved_ing.info + export_ing.info:
            findings.append(make_finding("VBA_PRESENT" if "VBA" in i else "INPUT_INFO", severity=Severity.INFO, message=i,
                                         explanation="Informational note about an input file.", suggested_fix="No action needed."))
        s = stage("scope_metadata", s)

        # learning hints (informational only; findings still count)
        for f in findings:
            if f.severity in (Severity.CRITICAL, Severity.HIGH):
                h = lstore.hint_for(f.rule_id, f.table_id, f.key, f.column)
                if h:
                    f.details["hint"] = h
        findings.sort(key=lambda f: f.sort_key())
        verdict = decide(findings)
        exit_code = EXIT_BY_VERDICT[verdict]

        # ---- summary
        lvl = Counter(f.level for f in findings)
        by_table_bad = {f.table_id for f in findings if f.level in ("CRITICAL", "HIGH", "BLOCKER", "NEEDS_MAPPING") and f.rule_id not in SCOPE_RULES and f.table_id}
        scope_bad = {f.table_id for f in findings if f.rule_id in SCOPE_RULES}
        analyzed = [p for p in pairs if p.approved is not None or p.jira]
        matched = [p for p in pairs if p.approved is not None and p.export is not None and p.id not in by_table_bad]
        stats: Counter[str] = Counter()
        for fr in fres.values():
            stats.update(fr.stats)
        summary = {
            "ticket": jira.ticket_key, "lob": lob, "states": list(jira.states), "filing_effective_date": jira.filing_effective_date,
            "renewal_effective_date": jira.renewal_effective_date,
            "rate_book": exp_meta.get("rate_book_code"), "export_edition": exp_meta.get("edition"), "export_effective_date": exp_meta.get("effective_date"),
            "baseline": baseline_info["label"],
            "tables_in_export": len(export_ing.book.tables), "tables_in_approved": len(approved_ing.book.tables),
            "tables_analyzed": len(analyzed), "tables_matched": len(matched), "tables_with_discrepancies": len(by_table_bad),
            "scope_violations": len(scope_bad),
            "cells_compared": stats["compared"], "cells_matched": stats["matched"], "rounded_matches": stats["rounded"],
            "critical": lvl["CRITICAL"], "high": lvl["HIGH"], "blockers": lvl["BLOCKER"], "warnings": lvl["WARNING"], "info": lvl["INFO"],
            "needs_mapping": lvl["NEEDS_MAPPING"], "needs_review": lvl["NEEDS_REVIEW"], "not_evaluated": lvl["NOT_EVALUATED"],
        }
        inputs = [InputRef(role="approved", name=f["name"], sha256=f["sha256"]) for f in approved_ing.input_files]
        inputs += [InputRef(role="export", name=f["name"], sha256=f["sha256"]) for f in export_ing.input_files]
        inputs += extra_inputs
        inputs.append(InputRef(role="jira", name=Path(opts.jira).name, sha256=hashlib.sha256(Path(opts.jira).read_bytes()).hexdigest()))
        sc = scope_matrix(pairs, statuses, deltas, findings)

        run_id = "R-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha256("".join(i.sha256 for i in inputs).encode()).hexdigest()[:4]
        proposals = [dict(p, run_id=run_id, lob=lob) for p in mapping.proposals]
        for r in mapping.resolved_jira:
            if r.confidence == "SUGGESTED":
                proposals.append({"kind": "TABLE_ALIAS", "status": "PROPOSED", "source": "jira", "jira_text": r.raw_text, "canonical_id": r.canonical_id, "run_id": run_id, "lob": lob})
        for ing in (approved_ing, export_ing):
            for iss in ing.issues:
                if iss.status == "HIDDEN":
                    proposals.append({"kind": "HIDDEN_SHEET", "status": "PROPOSED", "file": iss.file, "sheet": iss.sheet, "run_id": run_id, "lob": lob})
                if iss.status == "NEEDS_MAPPING":
                    proposals.append({"kind": "LAYOUT", "status": "NEEDS_MAPPING", "file": iss.file, "sheet": iss.sheet, "reason": iss.reason, "run_id": run_id, "lob": lob})
        result = RunResult(
            run_id=run_id, started_at=started, verdict=verdict, exit_code=exit_code, findings=findings, summary=summary, jira=jira,
            mapping=mapping, scope=sc, deltas=deltas, factor_results=fres, inputs=inputs, baseline_info=baseline_info,
            warnings=approved_ing.warnings + export_ing.warnings, info=approved_ing.info + export_ing.info, proposals=proposals,
            learned_used=learned.used, tolerance_notes=sorted(set(tol_notes)), timings=timings, cfg=cfg, cfg_hash=chash,
            approved_ing=approved_ing, export_ing=export_ing, out=opts.out, user=user, learned_version=learned.version, lob=lob,
        )
        # ---- scenarios
        from .tests_gen.generate import generate_tests
        result.tests = generate_tests(result)
        s = stage("tests", s)

        # ---- snapshot of this export (UNVERIFIED unless accepted)
        if snap is not None and export_ing.book.tables:
            ok = verdict in (Verdict.PASS, Verdict.PASS_WITH_WARNINGS, Verdict.PASS_WITH_LIMITATIONS)
            result.snapshot_id = snap.save(run_id, export_ing.book, opts.export.name, export_ing.input_files[0]["sha256"], verdict.value,
                                           "VERIFIED" if (opts.accept_snapshot and ok) else "UNVERIFIED", user, cfg.snapshots.retention_runs)
        s = stage("snapshot", s)
        # ---- learning bookkeeping
        confirmed = sum(1 for p in pairs if p.approved is not None and p.confidence == "CONFIRMED")
        lstore.record_run(run_id, __version__, chash, {i.name: i.sha256 for i in inputs}, verdict.value,
                          {"tables_confirmed": confirmed, "tables_mapped_total": sum(1 for p in pairs if p.approved is not None),
                           "manual_mappings": sum(1 for u in learned.used if u.get("kind") == "TABLE_ALIAS"), "findings": dict(lvl)})
        if opts.write_reports:
            from .report.writer import write_all
            write_all(result, opts.out)
        timings["total"] = round(time.perf_counter() - t0, 2)
        log.info("run finished: %s in %.1fs", verdict.value, timings["total"])
        return result
    finally:
        log.removeHandler(handler)
        handler.close()
        lstore.close()
        if snap is not None:
            snap.close()
