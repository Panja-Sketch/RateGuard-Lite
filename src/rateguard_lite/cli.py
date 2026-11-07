"""RateGuard-Lite command line (typer + rich)."""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404 - allowlisted: used only by `rgl ui` to start the local Streamlit server
import sys
import tempfile
import traceback
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .config import data_dir, load_config
from .errors import EXIT_INTERNAL, RGLError
from .guard.netguard import install_socket_guard
from .model.findings import GATE_TEXT, Verdict

app = typer.Typer(add_completion=False, no_args_is_help=True, help="RateGuard-Lite: verify a Guidewire rate book export against the approved workbook and Jira story. Local and offline.")
learn_app = typer.Typer(no_args_is_help=True, help="Inspect and share what the tool has learned.")
snap_app = typer.Typer(no_args_is_help=True, help="Snapshot History: the tool's own 'before' baseline.")
app.add_typer(learn_app, name="learn")
app.add_typer(snap_app, name="snapshot")
console = Console()
err = Console(stderr=True)

VERDICT_STYLE = {
    Verdict.PASS: "bold green", Verdict.PASS_WITH_WARNINGS: "bold yellow", Verdict.PASS_WITH_LIMITATIONS: "bold yellow",
    Verdict.NEEDS_REVIEW: "bold magenta", Verdict.FAIL: "bold red", Verdict.BLOCKED: "bold red",
}
LEVEL_STYLE = {"BLOCKER": "red", "CRITICAL": "red", "HIGH": "dark_orange", "NEEDS_MAPPING": "magenta", "NEEDS_REVIEW": "magenta",
               "NOT_EVALUATED": "yellow", "WARNING": "yellow", "INFO": "dim"}


def _utf8_streams() -> None:
    """D-025: never die on a console code page (cp1252) when output is redirected; unencodable characters become '?'."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf is not None:
            try:
                reconf(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _status(message: str) -> AbstractContextManager[Any]:
    """Rich spinner only on a real terminal (D-025); plain no-op otherwise."""
    return console.status(message) if sys.stdout.isatty() else nullcontext()


@app.callback()
def _main(ctx: typer.Context) -> None:
    _utf8_streams()
    install_socket_guard()


def _fail(exc: BaseException, debug: bool = False) -> None:
    if isinstance(exc, RGLError):
        err.print(Panel(Text.assemble(("What: ", "bold"), exc.what, "\n", ("Where: ", "bold"), exc.where or "-", "\n", ("Next: ", "bold"), exc.next_step or "-"),
                        title="RateGuard-Lite could not continue", border_style="red", box=box.ROUNDED))
        code = exc.exit_code
    else:
        err.print(Panel(f"An unexpected error occurred ({type(exc).__name__}). Re-run with --debug for details, and share run.log (it has no input data).",
                        title="Internal error", border_style="red"))
        code = EXIT_INTERNAL
    if debug:
        err.print(traceback.format_exc())
    raise typer.Exit(code)


# ---------------------------------------------------------------- run
@app.command()
def run(
    approved: Annotated[Path, typer.Option("--approved", help="Approved workbook: .xlsx/.xlsm, a folder of .xlsx, or a .zip")],
    jira: Annotated[Path, typer.Option("--jira", help="Jira story export: .txt, .md or .html")],
    export: Annotated[Path, typer.Option("--export", help="Guidewire 'Export to Spreadsheet' .xlsx (after implementation)")],
    out: Annotated[Path, typer.Option("--out", help="Output folder (must be new unless --overwrite)")],
    baseline: Annotated[Optional[Path], typer.Option("--baseline", help="Optional: export of the same rate book BEFORE the change")] = None,
    lob: Annotated[Optional[str], typer.Option("--lob", help="Override the line of business parsed from Jira")] = None,
    state: Annotated[Optional[str], typer.Option("--state", help="Override the 2-letter state parsed from Jira")] = None,
    profile: Annotated[str, typer.Option("--profile")] = "default",
    user: Annotated[Optional[str], typer.Option("--user")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    debug: Annotated[bool, typer.Option("--debug")] = False,
    accept_snapshot: Annotated[bool, typer.Option("--accept-snapshot", help="Opt in: save this export in Snapshot History as the accepted state (next run's baseline) if it passes")] = False,
) -> None:
    """Reconcile an implemented rate book against the approved workbook and the Jira story."""
    from .pipeline import RunOptions, run_reconciliation
    from .report.writer import verdict_text
    try:
        with _status("[bold]Starting...") as st:
            res = run_reconciliation(RunOptions(approved, jira, export, out, baseline, lob, state, profile, user, overwrite, debug, accept_snapshot),
                                     progress=(lambda m: st.update(f"[bold]{m}...")) if st is not None else None)
    except Exception as exc:  # noqa: BLE001
        _fail(exc, debug)
        return
    _print_result(res, verdict_text(res))
    console.print(f"\nReports written to [bold]{out}[/bold]  (open reconciliation_report.html)")
    if res.snapshot_id:
        passed = res.verdict in (Verdict.PASS, Verdict.PASS_WITH_WARNINGS, Verdict.PASS_WITH_LIMITATIONS)
        console.print(f"[dim]Snapshot {res.snapshot_id} recorded"
                      f"{' and accepted as the next baseline' if accept_snapshot and passed else ' (not accepted: only a passing run can be)' if accept_snapshot else ''}.[/dim]")
    raise typer.Exit(res.exit_code)


def _print_result(res: Any, text: str) -> None:
    style = VERDICT_STYLE[res.verdict]
    s = res.summary
    head = Text.assemble((f" {res.verdict.value.replace('_', ' ')} ", f"reverse {style}"), ("  " + GATE_TEXT[res.verdict], style))
    console.print(Panel(head, border_style=style.split()[-1], box=box.ROUNDED))
    facts = [("Ticket", s["ticket"]), ("LOB / State", f"{s['lob'] or '-'} / {','.join(s['states']) or '-'}"),
             ("Filing effective", s["filing_effective_date"]), ("Rate book", s["rate_book"]),
             ("Export edition", s["export_edition"]), ("Baseline", s["baseline"])]
    for label, v in facts:
        console.print(f"  [dim]{label:<17}[/dim]{v or '-'}")
    console.print(f"  [dim]{s['scope_check']}[/dim]")
    console.print()
    console.print(f"  [bold]{s['tables_analyzed']}[/bold] tables analyzed  [green]{s['tables_matched']} matched[/green]  "
                  f"[red]{s['tables_with_discrepancies']} with discrepancies[/red]  [red]{s['scope_violations']} scope violation(s)[/red]")
    console.print(f"  [bold]{s['cells_compared']:,}[/bold] cells compared  [red]{s['critical']} critical[/red]  [dark_orange]{s['high']} high[/dark_orange]  "
                  f"[yellow]{s['warnings']} warning(s)[/yellow]  {s['rounded_matches']} rounded match(es)")
    console.print()
    shown = [f for f in res.findings if f.level not in ("INFO",)][:12]
    if shown:
        ft = Table(box=box.SIMPLE_HEAD, title="Top findings")
        for h in ("Level", "Rule", "Table", "Detail"):
            ft.add_column(h, overflow="fold")
        for f in shown:
            ft.add_row(Text(f.level, style=LEVEL_STYLE.get(f.level, "")), f.rule_id, f.table_name or f.table_id, (f"{f.key}: " if f.key else "") + f.message)
        console.print(ft)
        rest = len([f for f in res.findings if f.level != "INFO"]) - len(shown)
        if rest > 0:
            console.print(f"[dim]... {rest} more in the reports[/dim]")


# ---------------------------------------------------------------- drift
@app.command()
def drift(
    left: Annotated[Path, typer.Option("--left")], right: Annotated[Path, typer.Option("--right")],
    out: Annotated[Path, typer.Option("--out")], lob: Annotated[Optional[str], typer.Option("--lob")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False, debug: Annotated[bool, typer.Option("--debug")] = False,
) -> None:
    """Environment drift: compare two Guidewire exports (e.g. SAASQA vs GWUpdate). No approved file needed."""
    from .engines.drift import run_drift
    from .ingest.gw_export import ingest_export
    from .report.drift_report import write_drift
    try:
        cfg = load_config()
        if out.exists() and any(out.iterdir()) and not overwrite:
            raise RGLError("The output folder already contains files.", where=out.name, next_step="Use a new --out or --overwrite.")
        out.mkdir(parents=True, exist_ok=True)
        with _status("Reading exports..."):
            a = ingest_export(left, cfg, "EXPORT", lob)
            b = ingest_export(right, cfg, "EXPORT", lob)
            res = run_drift(a.book, b.book, cfg, (lob or a.book.metadata.get("lob") or "GW").upper())
        write_drift(res, out, left.name, right.name)
    except Exception as exc:  # noqa: BLE001
        _fail(exc, debug)
        return
    t = Table(box=box.SIMPLE_HEAD, title=f"Drift: {left.name} vs {right.name}")
    t.add_column("Tables")
    t.add_column("Count", justify="right")
    for label, v in (("Identical", res.identical), ("Differing", res.differing), ("Only in left", res.only_left), ("Only in right", res.only_right),
                     ("Need mapping", res.needs_mapping)):
        t.add_row(label, str(len(v)))
    console.print(t)
    console.print(f"{len(res.diffs)} cell/row difference(s). Reports in [bold]{out}[/bold]")
    raise typer.Exit(0 if not (res.differing or res.only_left or res.only_right or res.needs_mapping) else 1)


# ---------------------------------------------------------------- inspect
@app.command()
def inspect(file: Annotated[Path, typer.Argument(help="A Guidewire export or approved workbook")],
            max_sheets: Annotated[int, typer.Option("--max-sheets")] = 15, debug: Annotated[bool, typer.Option("--debug")] = False) -> None:
    """Show detected sheets, header rows, columns/roles and metadata. Use to calibrate on a new Guidewire version."""
    from .guard.safety import safety_gate_workbook
    from .ingest.fastxlsx import FastWorkbookReader
    from .ingest.layout import ApprovedLayout, GwLayout, detect_approved_layout, detect_gw_layout, parse_type_token
    from .ingest.tablebuild import infer_columns, map_type
    try:
        cfg = load_config()
        rep = safety_gate_workbook(file, cfg.limits)
        for w in rep.warnings + rep.info:
            console.print(f"[yellow]note:[/yellow] {w}")
        with FastWorkbookReader(file, cfg.limits) as rd:
            inv = rd.inventory()
            console.print(f"[bold]{file.name}[/bold]: {len(inv)} sheet(s)")
            for i, s in enumerate(inv):
                if i >= max_sheets:
                    console.print(f"[dim]... {len(inv) - max_sheets} more sheet(s) (use --max-sheets)[/dim]")
                    break
                g = rd.grid(s.name)
                lay = detect_gw_layout(g, cfg.limits.max_header_scan_rows, cfg.dates.order)
                t = Table(title=f"{s.name}  [{s.visibility}]", box=box.SIMPLE_HEAD)
                if isinstance(lay, GwLayout):
                    console.print(f"[green]Guidewire layout[/green] type row {lay.type_row + 1}, header row {lay.header_row + 1}, data from row {lay.data_start + 1}, score {lay.score}")
                    console.print("  metadata: " + ", ".join(f"{k}={v}" for k, v in lay.metadata.items()))
                    hdr = g.rows[lay.header_row]
                    idx = [j for j, v in enumerate(hdr) if v not in (None, "")]
                    decl = {j: parse_type_token(g.rows[lay.type_row][j]) for j in idx if j < len(g.rows[lay.type_row])}
                    types = [map_type(tok[0]) if (tok := decl.get(j)) else map_type("STRING") for j in idx]
                    specs = infer_columns([str(hdr[j]) for j in idx], types, [tok[1] if (tok := decl.get(j)) else None for j in idx], "X", None)
                    for h in ("Column", "Canonical", "Role", "Type", "Scale"):
                        t.add_column(h)
                    for sp in specs:
                        t.add_row(sp.source_header, sp.canonical_name, sp.role.value, sp.type.value, str(sp.scale) if sp.scale is not None else "")
                    console.print(t)
                else:
                    alay = detect_approved_layout(g, cfg.limits.max_header_scan_rows)
                    if isinstance(alay, ApprovedLayout):
                        console.print(f"[cyan]Approved-style layout[/cyan] header row {alay.header_row + 1}, data from row {alay.data_start + 1}"
                                      + (", [yellow]multiple tables[/yellow]" if alay.multiple_tables else ""))
                        console.print("  headers: " + ", ".join(str(v) for v in g.rows[alay.header_row] if v not in (None, "")))
                    else:
                        console.print(f"[red]{s.name}: no layout detected.[/red] {lay.reason}")
    except Exception as exc:  # noqa: BLE001
        _fail(exc, debug)


# ---------------------------------------------------------------- review / map
def _review(run_dir: Path, only_mapping: bool, user: str | None) -> None:
    from .learning.review import apply_decision, describe, load_proposals, propose
    from .learning.store import LearningStore
    props = load_proposals(run_dir)
    if only_mapping:
        props = [p for p in props if p.get("status") == "NEEDS_MAPPING"]
    if not props:
        console.print("Nothing to review. All mappings are confirmed.")
        return
    store = LearningStore()
    done = 0
    try:
        for i, p in enumerate(props, 1):
            console.print(Panel(describe(p), title=f"Proposal {i} of {len(props)}", border_style="cyan"))
            choice = None
            if p.get("status") == "NEEDS_MAPPING" and p.get("candidates"):
                for j, c in enumerate(p["candidates"]):
                    console.print(f"  [{j + 1}] {c['names'][0]}  (score {c['score']})")
                console.print("  [0] none of these")
                pick = typer.prompt("Choose the matching export table", default="0")
                if pick.isdigit() and 0 < int(pick) <= len(p["candidates"]):
                    choice = int(pick) - 1
                    ans = "a"
                else:
                    ans = "r"
            else:
                ans = typer.prompt("[a]ccept / [r]eject / [s]kip", default="s").lower()[:1]
            if ans == "a":
                console.print("  " + apply_decision(store, p, "accept", choice, user))
                done += 1
            elif ans == "r":
                console.print("  " + apply_decision(store, p, "reject"))
            elif propose(store, p, user):
                console.print("  kept as PROPOSED (not in force); confirm later with `rgl learn confirm <id>`")
    finally:
        store.close()
    if done:
        from .learning.team import team_file
        console.print(f"[green]{done} item(s) confirmed[/green] and written to [bold]{team_file()}[/bold] (user and date recorded). "
                      "Commit that file so the whole team gets the mapping. Re-run the reconciliation to use it.")
    else:
        console.print("0 items confirmed.")


@app.command("map")
def map_cmd(run: Annotated[Path, typer.Option("--run", help="Output folder of a previous run")], user: Annotated[Optional[str], typer.Option("--user")] = None) -> None:
    """Interactively resolve NEEDS_MAPPING items from a run."""
    _review(run, True, user)


@app.command()
def review(run_dir: Annotated[Path, typer.Argument(help="Output folder of a previous run")], user: Annotated[Optional[str], typer.Option("--user")] = None) -> None:
    """Accept, edit or reject mapping proposals (proposals.json)."""
    _review(run_dir, False, user)


# ---------------------------------------------------------------- learn
@learn_app.command("list")
def learn_list(status: Annotated[Optional[str], typer.Option("--status", help="CONFIRMED (team file), PROPOSED, PROMOTED, RETIRED")] = None,
               kind: Annotated[Optional[str], typer.Option("--kind")] = None) -> None:
    """Confirmed team mappings (config/team_mappings.yaml) and local proposals."""
    from .learning.store import LearningStore
    st = LearningStore()
    t = Table(box=box.SIMPLE_HEAD)
    for h in ("ID", "Kind", "LOB", "Table", "Key", "Value", "Status", "By", "On"):
        t.add_column(h, overflow="fold")
    try:
        if status in (None, "CONFIRMED"):
            for i in st.confirmed_items():
                if kind and i["kind"] != kind:
                    continue
                t.add_row(str(i["id"]), i["kind"], i.get("scope_lob") or "", i.get("scope_table") or "", i["key"], json.dumps(i["value"]),
                          "CONFIRMED" + (" (legacy db)" if i["source"] != "team" else ""), i.get("confirmed_by") or "", str(i.get("confirmed_on") or ""))
        for r in st.list(kind, status):
            if r["status"] == "CONFIRMED":
                continue
            if status is None and r["status"] in ("PROMOTED", "RETIRED"):
                continue
            t.add_row(str(r["id"]), r["kind"], r["scope_lob"] or "", r["scope_table"] or "", r["key"], r["value_json"], r["status"],
                      r["confirmed_by"] or r["created_by"] or "", (r["confirmed_at"] or r["created_at"] or "")[:10])
    finally:
        st.close()
    console.print(t)


@learn_app.command("show")
def learn_show(item_id: str) -> None:
    from .learning.store import LearningStore
    st = LearningStore()
    try:
        if item_id.startswith("T-"):
            found = [i for i in st.team_items() if i["id"] == item_id]
            console.print_json(json.dumps(found[0], default=str) if found else "{}")
        else:
            r = st.get(int(item_id))
            console.print_json(json.dumps(dict(r)) if r else "{}")
    finally:
        st.close()


@learn_app.command("confirm")
def learn_confirm(item_id: int, user: Annotated[Optional[str], typer.Option("--user")] = None) -> None:
    """Promote a local PROPOSED item into the team mappings file."""
    from .learning.store import LearningStore
    from .learning.team import team_file
    st = LearningStore()
    try:
        tid = st.confirm(item_id, user)
    except RGLError as exc:
        _fail(exc)
        return
    finally:
        st.close()
    console.print(f"Item {item_id} confirmed as {tid} in [bold]{team_file()}[/bold]. Commit that file to share it.")


@learn_app.command("retire")
def learn_retire(item_id: str, user: Annotated[Optional[str], typer.Option("--user")] = None) -> None:
    """Remove a team mapping (ID like T-1a2b3c) or retire a local proposal (numeric ID)."""
    from .learning.store import LearningStore
    st = LearningStore()
    try:
        ok = st.retire(item_id, user)
    finally:
        st.close()
    console.print(f"Item {item_id} {'retired' if ok else 'not found'}.")


@learn_app.command("export")
def learn_export(out: Annotated[Path, typer.Option("--out")]) -> None:
    """Write every mapping in force to a file (handy for moving legacy local items into the team file)."""
    from .learning.store import LearningStore
    st = LearningStore()
    n = st.export_yaml(out)
    st.close()
    console.print(f"Exported {n} confirmed item(s) to {out}")


@learn_app.command("import")
def learn_import(file: Path, trust: Annotated[bool, typer.Option("--trust", help="Write straight into the team file as CONFIRMED instead of keeping as PROPOSED")] = False,
                 user: Annotated[Optional[str], typer.Option("--user")] = None) -> None:
    from .learning.store import LearningStore
    st = LearningStore()
    try:
        n = st.import_yaml(file, trust, user)
    except RGLError as exc:
        _fail(exc)
        return
    finally:
        st.close()
    console.print(f"Imported {n} item(s) as {'CONFIRMED (team file)' if trust else 'PROPOSED (review with `rgl learn list --status PROPOSED`, then `rgl learn confirm <id>`)'}.")


@learn_app.command("diff")
def learn_diff(file: Path) -> None:
    """Show what another mappings file would add to the team mappings."""
    import yaml

    from .learning.store import LearningStore
    st = LearningStore()
    mine = {(i["kind"], i["key"], i.get("scope_lob") or "", i.get("scope_table") or "", json.dumps(i["value"], sort_keys=True)) for i in st.confirmed_items()}
    data = yaml.safe_load(Path(file).read_text(encoding="utf-8")) or {}
    new = [it for it in data.get("items", []) if (it["kind"], it["key"], it.get("scope_lob") or "", it.get("scope_table") or "", json.dumps(it["value"], sort_keys=True)) not in mine]
    st.close()
    console.print(f"{len(new)} item(s) in {file.name} are not in your team mappings:")
    for it in new[:50]:
        console.print(f"  + {it['kind']} {it['key']} -> {it['value']}")


@app.command()
def stats() -> None:
    """Learning metrics: proof the tool is improving."""
    from .learning.store import LearningStore
    st = LearningStore()
    s = st.stats()
    st.close()
    t = Table(box=box.SIMPLE_HEAD, show_header=False)

    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v * 100:.0f}%"

    t.add_row("Runs recorded", str(s["runs"]))
    t.add_row("Auto-mapped table rate", pct(s["auto_mapped_rate"]))
    t.add_row("Suggestion acceptance rate", pct(s["suggestion_acceptance_rate"]))
    t.add_row("Median manual mappings per run", str(s["median_manual_mappings_per_run"]))
    t.add_row("Manual mappings over time", " ".join(str(x) for x in s["manual_mappings_over_time"][-12:]) or "-")
    for rule, v in s["false_positive_rate_by_rule"].items():
        t.add_row(f"False-positive rate: {rule}", pct(v))
    console.print(t)


# ---------------------------------------------------------------- snapshots
@snap_app.command("list")
def snap_list() -> None:
    from .snapshots.store import SnapshotStore
    st = SnapshotStore()
    t = Table(box=box.SIMPLE_HEAD)
    for h in ("Snapshot", "Rate book", "Edition", "Effective", "Status", "Verdict", "Tables", "Taken", "Accepted by"):
        t.add_column(h, overflow="fold")
    for s in st.list():
        t.add_row(s.id, s.book_key, s.edition, s.effective_date, s.status, s.verdict or "", str(s.tables), s.taken_at[:16], s.accepted_by or "")
    st.close()
    console.print(t)


@snap_app.command("show")
def snap_show(snapshot_id: str) -> None:
    from .snapshots.store import SnapshotStore
    st = SnapshotStore()
    s = st.get(snapshot_id)
    rows = list(st.db.execute("SELECT native_id, table_code, row_count, substr(sha,1,12) h FROM snapshot_table WHERE snapshot_id=? ORDER BY native_id", (snapshot_id,)))
    st.close()
    if s is None:
        _fail(RGLError(f"Snapshot {snapshot_id} not found.", next_step="Run `rgl snapshot list`."))
        return
    console.print(s)
    t = Table(box=box.SIMPLE_HEAD)
    for h in ("Table", "Code", "Rows", "Hash"):
        t.add_column(h)
    for r in rows[:100]:
        t.add_row(r["native_id"], r["table_code"], str(r["row_count"]), r["h"])
    console.print(t)


@snap_app.command("take")
def snap_take(export: Annotated[Path, typer.Option("--export")], lob: Annotated[Optional[str], typer.Option("--lob")] = None,
              user: Annotated[Optional[str], typer.Option("--user")] = None) -> None:
    """Bootstrap: record a QA/Prod export as an accepted baseline BEFORE a developer loads changes."""
    import datetime

    from .ingest.gw_export import ingest_export
    from .snapshots.store import SnapshotStore
    try:
        cfg = load_config()
        with _status("Reading export..."):
            ing = ingest_export(export, cfg, "EXPORT", lob)
        if ing.issues and any(i.status == "NEEDS_MAPPING" for i in ing.issues):
            raise RGLError("Some sheets could not be read, so the snapshot would be incomplete.", where=export.name, next_step="Run `rgl inspect` on the file first.")
        st = SnapshotStore()
        sid = st.save("T-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), ing.book, export.name, ing.input_files[0]["sha256"], "PASS", "VERIFIED", user, cfg.snapshots.retention_runs)
        st.close()
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    if not cfg.snapshots.enabled:
        console.print("[yellow]Note:[/yellow] Snapshot History is off (snapshots.enabled: false), so runs will not use this snapshot unless you run with `--profile snapshots` or enable it in config.")
    console.print(f"[green]Snapshot {sid} recorded[/green] ({len(ing.book.tables)} tables, edition {ing.book.metadata.get('edition', '?')}). It will be used as the baseline for the next run.")


@snap_app.command("accept")
def snap_accept(snapshot_id: str, user: Annotated[Optional[str], typer.Option("--user")] = None) -> None:
    from .snapshots.store import SnapshotStore
    st = SnapshotStore()
    try:
        st.accept(snapshot_id, user)
    except RGLError as exc:
        _fail(exc)
        return
    finally:
        st.close()
    console.print(f"[green]{snapshot_id} accepted.[/green]")


@snap_app.command("purge")
def snap_purge(keep: Annotated[int, typer.Option("--keep", help="Keep this many newest snapshots per rate book")] = 0,
               all_: Annotated[bool, typer.Option("--all")] = False, yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    from .snapshots.store import SnapshotStore
    if not yes and not typer.confirm("Delete stored snapshots?", default=False):
        raise typer.Exit(0)
    st = SnapshotStore()
    n = st.purge(keep=keep, all_=all_)
    st.close()
    console.print(f"Removed {n} snapshot(s).")


# ---------------------------------------------------------------- ui / selftest / sample
@app.command()
def ui(port: Annotated[int, typer.Option("--port")] = 8501) -> None:
    """Start the local web UI (bound to 127.0.0.1 only)."""
    app_py = Path(__file__).parent / "ui" / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_py), "--server.address", "127.0.0.1", "--server.port", str(port),
           "--server.headless", "true", "--browser.gatherUsageStats", "false", "--server.maxUploadSize", str(load_config().limits.max_file_mb),
           "--global.developmentMode", "false"]
    console.print(f"Starting RateGuard-Lite at [bold]http://127.0.0.1:{port}[/bold]  (Ctrl+C to stop). Files stay on this machine.")
    raise typer.Exit(subprocess.call(cmd))  # noqa: S603


CORE_SELFTEST = ["clean_pass", "factor_mismatch", "order_of_magnitude", "rounded_match_only", "missing_row", "off_by_one_range", "bypass_violation",
                 "snapshot_baseline_undeclared_change", "snapshot_first_run_limitations", "wildcard_to_blank", "duplicate_key", "unsafe_zip_traversal",
                 "unmappable_table_blocked", "effective_date_mismatch", "no_baseline_strict_limitations",
                 "out_of_scope_table_dated"]


@app.command()
def selftest(full: Annotated[bool, typer.Option("--full", help="Run all fixture scenarios")] = False,
             tables: Annotated[int, typer.Option("--tables")] = 30) -> None:
    """Run the bundled synthetic scenarios and check each produces its expected verdict."""
    from .samples.generator import SCENARIOS, build_scenario
    from .samples.runner import check, run_scenario
    names = [n for n in SCENARIOS if n != "export_220_tables_perf"] if full else CORE_SELFTEST
    work = Path(tempfile.mkdtemp(prefix="rgl_selftest_"))
    bad = 0
    t = Table(box=box.SIMPLE_HEAD, title=f"RateGuard-Lite self-test (v{__version__})")
    for h in ("Scenario", "Expected", "Got", "Result"):
        t.add_column(h)
    try:
        with _status("Running scenarios...") as stt:
            for n in names:
                if stt is not None:
                    stt.update(f"Running {n}...")
                sc = build_scenario(n, work / "gen", tables=tables)
                o = run_scenario(sc, work / "run")
                probs = check(sc, o)
                bad += bool(probs)
                t.add_row(n, sc.expected_verdict, o.verdict, "[green]ok[/green]" if not probs else "[red]" + "; ".join(probs) + "[/red]")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    console.print(t)
    console.print("[bold green]Self-test passed.[/bold green]" if not bad else f"[bold red]{bad} scenario(s) failed.[/bold red]")
    raise typer.Exit(0 if not bad else 1)


@app.command()
def sample(out: Annotated[Path, typer.Option("--out")], tables: Annotated[int, typer.Option("--tables")] = 60) -> None:
    """Write synthetic sample inputs (no real carrier data) for training and demos."""
    from .samples.generator import write_sample
    scs = write_sample(out, tables)
    console.print(f"Sample inputs written to [bold]{out}[/bold]")
    for sc in scs:
        console.print(f"\n[bold]{sc.name}[/bold]  (expected verdict: {sc.expected_verdict})")
        console.print(f"  rgl run --approved {sc.approved} --jira {sc.jira} --export {sc.export} --baseline {sc.baseline} --out {out / (sc.name + '_result')}")


@app.command()
def version() -> None:
    """Show version and data folder."""
    console.print(f"RateGuard-Lite {__version__}\nData folder: {data_dir()}")


def main() -> None:
    try:
        app()
    except RGLError as exc:
        _fail(exc)


if __name__ == "__main__":
    main()
