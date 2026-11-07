# RateGuard-Lite — Locked Build Specification (v1.1)

> **v1.1 changes (confirmed by the product owner):** (1) In the normal workflow **no "before" export exists**: only the Jira story, the approved workbook and the QA export after implementation. The tool therefore builds its own baseline from previous runs (**Section 8.7, Snapshot History**), and `--baseline` stays optional. (2) Approved workbooks **always contain whole tables** (completeness is always FULL). (3) Strikethrough = flag for review. (4) Mid-term endorsements stay on the term-inception rate book. (5) Python is allowed on user machines. (6) Real Guidewire exports have **50–200+ table sheets**; fixtures and performance targets reflect this.

> **Instructions to the coding assistant:** Build this project exactly as specified. Where this document says MUST, do not deviate. Where it says CONFIGURABLE, implement the config and the stated default. If something is genuinely ambiguous, choose the option that **fails closed** (reports `NEEDS_REVIEW` / `NOT_EVALUATED`, never a silent `PASS`) and record the decision in `docs/DECISIONS.md`. Build in the phase order in Section 16 and keep all tests green after each phase.

---

## 0. One-paragraph purpose

RateGuard-Lite is a **local, offline** tool that verifies a Guidewire PolicyCenter rate book export against the **approved rate workbook** and the **Jira story** for a rate change. It reports, cell by cell, whether every approved factor landed correctly. It checks that Guidewire changed only what the story declared, that bypassed tables were left alone, and that the edition and effective date match the filing. It also generates a targeted QA test script for the changed rows. It runs on the user's own Windows/macOS/Linux machine, and **input documents never leave that machine**.

## 1. Scope

### In scope (v1)
1. Approved workbook ingestion: one multi-tab `.xlsx`/`.xlsm`, **or** a folder/`.zip` of `.xlsx` files.
2. Guidewire "Export to Spreadsheet" `.xlsx` ingestion (the *candidate*, after implementation).
3. **Optional** baseline Guidewire export (`--baseline`: the prior edition, or the same rate book from another environment).
4. Jira story ingestion: plain text, Markdown, or HTML (Jira's "Export → Word/HTML" or copy-paste).
5. Table mapping (approved ↔ Jira ↔ Guidewire) with alias learning.
6. Cell-level factor reconciliation, scope audit, range integrity, date/edition audit.
7. Environment drift mode: export vs. export (e.g. SAASQA vs GWUpdate), no approved file needed.
8. Smart test-scenario generation (data-driven quote parameters plus transaction/date matrix).
9. Reports: console verdict, JSON, Excel, self-contained HTML.
10. A local feedback/learning store with user confirmation.
11. CLI (primary) and a local Streamlit UI bound to `127.0.0.1` only.

### Out of scope (v1). MUST NOT be built
- Any cloud deployment, telemetry, analytics, or network calls (see Section 11).
- Calling Guidewire APIs or computing premiums. This tool compares **tables**, not rating algorithms.
- Rate routine (Gosu) analysis.
- Auto-posting to Jira. The tool may *write* a Jira-comment text file for the user to paste.
- Any ML model that changes comparison outcomes.

---

## 2. Inputs (contract)

| ID | CLI flag | Required | Format | Meaning |
|---|---|---|---|---|
| I1 | `--approved PATH` | Yes (except `drift` mode) | `.xlsx`, `.xlsm` (data only), a folder of `.xlsx`, or a `.zip` of `.xlsx` | Actuarial/product approved tables. **Usually contains only the added or modified tables.** |
| I2 | `--jira PATH` | Yes (except `drift` mode) | `.txt`, `.md`, `.html` | Summary, description and comments of the story |
| I3 | `--export PATH` | Yes | `.xlsx` | Guidewire PolicyCenter export of the implemented rate book (candidate) |
| I4 | `--baseline PATH` | Optional (rarely available) | `.xlsx` | Guidewire export of the same rate book **before** the change (e.g. Prod export). If omitted, the tool uses **Snapshot History** (8.7) as the baseline when one exists |
| — | `--lob CODE` | Optional | text | Overrides the LOB parsed from Jira |
| — | `--state XX` | Optional | 2-letter | Overrides the state parsed from Jira |
| — | `--profile NAME` | Optional | text | Selects the config profile (default `default`) |
| — | `--out DIR` | Yes | folder | Output folder (created; MUST NOT already contain a previous run unless `--overwrite`) |

**Comparison semantics (MUST):**

"Baseline" below means: `--baseline` if given, else the latest **verified snapshot** of the same rate book from Snapshot History (8.7), else none.

| Check | Needs | If no baseline |
|---|---|---|
| Every approved row/factor landed correctly (full table) | I1 + I3 | — (always evaluated: this is the primary gate) |
| Extra/missing rows in an approved table | I1 + I3 | — (always evaluated, because approved tables are FULL) |
| Approved table missing in export | I1 + I3 | — |
| Jira-declared table missing from the approved file | I1 + I2 | — |
| Undeclared change to a table **not** in the approved file / Jira | I3 + baseline | `NOT_EVALUATED` |
| Bypassed table modified | I3 + baseline | Fallback: `NEEDS_REVIEW` if that table's metadata (edition/effective date) in I3 equals the new filing date; else `NOT_EVALUATED` |
| Edition / effective date matches the filing | I2 + I3 | — |

A "missing update" (declared table not changed) needs no baseline: approved tables are full, so if the export equals the approved table, the intended state is in place.

A `NOT_EVALUATED` check MUST prevent an overall `PASS`. Without any baseline, the best verdict is `PASS_WITH_LIMITATIONS` ("all approved values verified; changes to other tables not checked"). The report MUST say how to get the scope checks: the next run of this rate book will have a snapshot, or the user can supply `--baseline`.

---

## 3. Core design decision: canonical Rate Table Model (not IPIR)

Do **not** implement a rating-algorithm IR. Every source (approved, export, baseline) is normalized into this model and only this model is compared.

```python
# pydantic v2, all models: model_config = ConfigDict(extra="forbid", frozen=True)

class SourceRef(BaseModel):
    file: str            # file name only, no absolute path in reports
    sheet: str
    cell_range: str      # e.g. "A9:F120"
    sha256: str          # of the source file

class ColumnRole(StrEnum):
    KEY = "KEY"                  # exact-match parameter
    RANGE_MIN = "RANGE_MIN"
    RANGE_MAX = "RANGE_MAX"
    FACTOR = "FACTOR"            # value being compared
    IGNORE = "IGNORE"            # notes, descriptions, audit columns

class ColumnType(StrEnum):
    STRING = "STRING"; INTEGER = "INTEGER"; DECIMAL = "DECIMAL"; DATE = "DATE"; BOOLEAN = "BOOLEAN"

class ColumnSpec(BaseModel):
    canonical_name: str          # normalized (see 5.4)
    source_header: str           # exactly as in file
    role: ColumnRole
    type: ColumnType
    scale: int | None = None     # decimal places for DECIMAL factors (from GW DataType or config)
    wildcard_tokens: frozenset[str] = frozenset()   # per-column; see 5.5
    range_convention: Literal["INCLUSIVE_INCLUSIVE","INCLUSIVE_EXCLUSIVE"] | None = None

class CellValue(BaseModel):
    kind: Literal["VALUE","WILDCARD","BLANK"]
    value: str | None            # canonical string form (Decimal as normalized string, ISO date, upper-trimmed string)
    raw: str | None              # original text for evidence
    coord: str                   # e.g. "C14"
    flags: frozenset[str] = frozenset()   # e.g. {"STRIKETHROUGH","FORMULA_CACHED","HIDDEN_ROW","HIGHLIGHTED"}

class Row(BaseModel):
    key: tuple[CellValue, ...]   # ordered by key columns (KEY, RANGE_MIN, RANGE_MAX in column order)
    factors: dict[str, CellValue]
    source_row: int

class RateTable(BaseModel):
    canonical_id: str            # e.g. "BOP:121" or learned alias id
    display_name: str
    origin: Literal["APPROVED","EXPORT","BASELINE"]
    source: SourceRef
    columns: tuple[ColumnSpec, ...]
    rows: tuple[Row, ...]
    metadata: dict[str, str]     # GW: RateBookCode, Edition, TableCode, EffectiveDate, etc. (normalized)
    completeness: Literal["FULL","PARTIAL","UNKNOWN"]   # see 6.4
    warnings: tuple[str, ...]

class RateBook(BaseModel):
    origin: Literal["APPROVED","EXPORT","BASELINE"]
    tables: dict[str, RateTable]           # by canonical_id
    metadata: dict[str, str]
    unmapped_sheets: tuple[str, ...]
```

All numeric handling MUST use `decimal.Decimal` built from the cell's **text representation**, never from Python float arithmetic. When openpyxl returns a float, convert with `Decimal(repr(value))`, then normalize. No pandas in the comparison path. pandas is allowed only for report writing, if at all.

---

## 4. Tech stack (locked)

| Concern | Choice | Notes |
|---|---|---|
| Language | Python **3.11+** (3.12 recommended) | |
| Packaging | `pyproject.toml`, installable with `pip install -e .`; optional `uv` | Pin exact versions in `requirements.lock` |
| Excel read | `openpyxl` (normal mode, `data_only=True` **and** a second `data_only=False` pass for formula detection) | Needed for strikethrough, fills, hidden rows/sheets, merged cells |
| XML safety | `defusedxml` installed (openpyxl uses it automatically) | Verify at startup |
| Models | `pydantic` v2 | |
| Fuzzy matching | `rapidfuzz` | |
| Dates | `python-dateutil` (strict wrapper, see 5.6) | |
| HTML parsing (Jira) | `beautifulsoup4` + `lxml` | Text extraction only, no JS |
| CLI | `typer` + `rich` | |
| UI | `streamlit` | Bind `127.0.0.1`, telemetry disabled |
| Reports | `openpyxl` (Excel), `jinja2` (HTML, autoescape on) | |
| Feedback store | `sqlite3` (stdlib) | |
| Tests | `pytest`, `pytest-cov`, `hypothesis` | Coverage ≥ 85% on `core/` |
| Lint/type | `ruff`, `mypy --strict` on `core/` | |

No other runtime dependencies without recording the reason in `docs/DECISIONS.md`.

---

## 5. Ingestion & normalization

### 5.1 File safety gate (MUST run before any parser)
Reject with a typed error (`UnsafeInputError`) and exit code 3 if any of these hold:
- Extension not in the allowed set for that input; or magic bytes are not ZIP (`PK\x03\x04`) for xlsx/xlsm/zip.
- File size > `limits.max_file_mb` (default 100 MB); `.zip` total uncompressed > `limits.max_unzipped_mb` (default 500 MB); compression ratio > 100:1 on any member; member count > 2,000.
- ZIP member paths that are absolute or contain `..` (path traversal).
- Encrypted/password-protected workbook.
- **Do not reject** `.xlsm` approved files (actuaries use them), but NEVER execute or load VBA. Record `vbaProject.bin` presence as an INFO warning.
- External links (`xl/externalLinks/`), OLE objects, or data connections → WARNING in the report (values are still read from cached cells); never follow them.
- `.zip` approved bundles: extract only `.xlsx`/`.xlsm` members into a temp directory created with `tempfile.mkdtemp()`, delete it afterward (try/finally), and ignore other members with a warning.
- Total cells read per workbook > `limits.max_cells` (default 2,000,000) → abort.

### 5.2 Sheet inventory
For each workbook record: sheet name, visibility (visible/hidden/veryHidden), tab color, used range, merged ranges, row count. Hidden sheets are **inventoried but not mapped** unless the user confirms the mapping (then they're used with a `HIDDEN_SHEET` flag).

### 5.3 Header / table region detection
The tool must find where each table's header row and data rows are.

**Guidewire export (I3/I4):** the export has a metadata block before the data. The exact layout varies by Guidewire version and configuration, so **it MUST NOT be hard-coded**. Implement:
1. A **layout profile** in config (`gw_layouts:`). Each profile lists metadata label patterns (e.g. `RateBookCode`, `Edition`, `Table Code`, `Effective Date`, `Parameter`, `DataType`), the marker that identifies the header row, and the type-declaration row tokens (`DECIMAL`, `INTEGER`, `STRING`, `DATE`, `BOOLEAN`, plus configurable synonyms).
2. **Detection algorithm:** scan the first `max_header_scan_rows` (default 30) rows. Score each row:
   - +3 if ≥ 60% of its non-empty cells are type tokens → candidate type row
   - +2 if the row contains a factor marker (configurable: `Factor`, `Rate`, `Value`, `Premium`)
   - +1 per known metadata label at column A
   The header row is the row immediately above or below the type row, according to the profile. Data starts at the first row after the header/type rows that has ≥ 1 non-empty key cell.
3. If the best score < threshold, or two candidates tie → table status `NEEDS_MAPPING` and the user must confirm through `rgl map` (Section 9). Never guess.
4. Metadata label/value pairs found in the metadata block are captured into `RateTable.metadata` (normalized keys: `rate_book_code`, `edition`, `table_code`, `effective_date`, `expiration_date`, `status`, `jurisdiction`, `lob`).
5. A confirmed layout is saved as a learned layout fingerprint (Section 9) and reused automatically next time.

**Approved workbook (I1):** default is header in row 1; detection uses the same scoring without type rows (header = first row where ≥ 50% of cells are non-numeric text and the following rows are mostly numeric/code values). Title rows, notes and blank rows above the header are skipped and recorded. Multiple tables on one sheet (stacked or side by side) → `NEEDS_MAPPING` in v1 unless a learned layout exists.

### 5.4 Column normalization & roles
- Canonical column name: NFKC normalize → lowercase → strip → collapse whitespace/underscores/hyphens to `_` → remove punctuation except `_`. A configurable synonym map is then applied (`terr`→`territory`, `class`→`class_code`, `stories`→`number_of_stories`, etc.).
- Range pairs: columns whose canonical names end with `_min`/`_max`, `_from`/`_to`, `_low`/`_high`, `lower`/`upper`, `minimum`/`maximum`, or headers like `Stories From` / `Stories To`, are paired into `RANGE_MIN`/`RANGE_MAX`.
- Role inference order: (1) learned/confirmed column roles; (2) GW type row plus profile factor markers; (3) heuristic: the right-most numeric column(s) whose header matches a factor marker = `FACTOR`; all others = `KEY`; free-text "description/notes/comment" columns = `IGNORE`.
- If the approved table and the export table end up with **different key-column sets** after synonym mapping → `NEEDS_MAPPING` (column-level), never a silent comparison.

### 5.5 Value normalization
- Strings: NFKC, trim, collapse internal whitespace, uppercase for KEY columns (configurable per column). Leading zeros are **preserved** for code columns (class codes, territories, zips). If openpyxl yields a number for a column typed STRING, convert using the cell's `number_format` to restore zero padding (e.g. format `000` → `012`); if not determinable, flag `POSSIBLE_LEADING_ZERO_LOSS`.
- Decimals: `Decimal` from text; strip `%` (divide by 100 only if the column is configured `percent: true`), thousands separators and currency symbols; reject scientific notation unless the column is DECIMAL.
- Booleans: `{Y,YES,TRUE,1}`/`{N,NO,FALSE,0}`, configurable.
- **Wildcards are per column.** Global default tokens: `""` (blank), `*`, `ALL`, `ANY`, `DEFAULT`, `<ALL>`. `-1`/`0`/`NULL` are wildcards **only** if listed for that column in config/learned mapping. The approved and export sides may use different tokens; both normalize to `kind="WILDCARD"`.
- BLANK vs WILDCARD: a blank KEY cell is WILDCARD only if blank is enabled for that column (default yes for KEY columns). A blank FACTOR cell is `BLANK` and always an issue (`BLANK_FACTOR`, HIGH), because a past production bug came from blanks loaded instead of wildcards.
- Formula cells: use the cached value; flag `FORMULA_CACHED` (INFO). A formula with no cached value → `FORMULA_NO_VALUE` (BLOCKER for that table, since we cannot know the approved value).
- Excel error values (`#N/A`, `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`) → `EXCEL_ERROR` (BLOCKER for that row).
- Merged cells inside the data region → fill down only for KEY columns and flag `MERGED_KEY_FILLED` (WARNING); merged FACTOR cells → `NEEDS_REVIEW`.
- **Approved-side formatting signals (MUST capture):** strikethrough font on a row/cell → `STRIKETHROUGH` flag (the row is treated as *intended deletion* if configured `strikethrough_means: delete`, otherwise `NEEDS_REVIEW`; default `NEEDS_REVIEW`). Fill colors are recorded as `HIGHLIGHTED:<rgb>` hints, used only for reporting ("highlighted rows changed") and never for pass/fail.
- Hidden rows/columns in data regions → included and flagged `HIDDEN_ROW`/`HIDDEN_COL` (WARNING).

### 5.6 Dates
Strict parse to ISO `YYYY-MM-DD`. Accept: Excel date cells, `YYYY-MM-DD[ hh:mm:ss[.fff]]`, `M/D/YYYY`, `MM/DD/YYYY`, `M-D-YYYY`, `Month D, YYYY`, `Mon D YYYY`. **US month-first is the default** (configurable). Two-digit years and ambiguous strings that cannot be resolved under the configured order → `AMBIGUOUS_DATE` (NEEDS_REVIEW). A time component other than 00:00:00 → WARNING.

### 5.7 Duplicate keys
Two rows in the same table with the same composite key → `DUPLICATE_KEY` (BLOCKER for that table side). Identical duplicates in an export are still reported (WARNING), because Guidewire should not hold them.

---

## 6. Table mapping (approved ↔ Jira ↔ Guidewire)

### 6.1 Canonical table identity
A canonical table ID is `"{LOB}:{number}"` when a table number is extractable, else `"{LOB}:{slug}"`. The **alias store** maps every known name (approved tab name, Jira mention, GW table code, GW table name) → canonical ID, per LOB.

### 6.2 Candidate extraction from a name
- Table number: regex for a leading/standalone number: `^\s*(?:table\s*)?(\d{1,4})\b`, `\btable\s*#?\s*(\d{1,4})\b`, `_(\d{1,4})_`. Record but **do not trust alone**: numbers collide across LOBs.
- Name tokens: normalized words with stopwords removed (`table`, `rate`, `factor` stay, configurable), expanded through the abbreviation map (`BLD`→`BUILDING`, `BPP`→`BUSINESS_PERSONAL_PROPERTY`, `EPLI`, `GL`, `TERR`, etc.).

### 6.3 Matching algorithm (deterministic, explainable)
For each approved table A and each export table E, compute:
```
score = 0.45 * alias_hit        (1 if a confirmed alias maps both to the same canonical id, else 0)
      + 0.20 * number_match     (1 if extracted table numbers are equal and non-null)
      + 0.20 * name_similarity  (rapidfuzz token_set_ratio of expanded tokens / 100)
      + 0.15 * column_overlap   (Jaccard of canonical KEY column names)
```
- `alias_hit == 1` → **mapped (CONFIRMED)** regardless of the other terms.
- else best `score ≥ 0.80` **and** margin over the second-best ≥ 0.10 → **mapped (SUGGESTED)**. Usable, but the report lists it and asks for confirmation; the verdict cannot be better than `PASS_WITH_WARNINGS` until confirmed.
- else → `UNMAPPED` → `NEEDS_MAPPING`, verdict `BLOCKED` for that table.
- One-to-one constraint: solve as an assignment (greedy by score with the margin rule is acceptable); conflicts → `NEEDS_MAPPING`.
- Jira table references map to canonical IDs the same way (number + name similarity) and inherit `CONFIRMED` only through the alias store.

### 6.4 Approved table completeness (locked: FULL)
Approved workbooks **always contain the whole table**. Every approved table is `FULL`:
- every export key not in approved → `EXTRA_ROW_IN_EXPORT` (HIGH);
- every approved key not in export → `MISSING_ROW_IN_EXPORT` (HIGH).

Sanity guard: if an approved table has < 50% of the export table's row count, emit `APPROVED_TABLE_SUSPICIOUSLY_SMALL` (NEEDS_REVIEW): it may be a partial paste or a wrong mapping. Keep a config switch `completeness.mode: FULL` (only value supported in v1) so PARTIAL can be added later without refactoring.

---

## 7. Jira story parser (deterministic)

Input text is converted to plain text while **preserving strikethrough markers**:
- HTML: `<del>`, `<s>`, `<strike>`, and `style="text-decoration: line-through"` → wrap the text in `~~ ~~`. Tables → rows joined with ` | `. Scripts/styles removed.
- Jira wiki markup: `-text-` (strikethrough) → `~~text~~`.
- Markdown: keep `~~ ~~`.

Extract into `JiraScope` (pydantic):
```python
class TableAction(StrEnum): ADD="ADD"; MODIFY="MODIFY"; REPLACE="REPLACE"; REMOVE="REMOVE"; BYPASS="BYPASS"; MENTION="MENTION"

class JiraTableRef(BaseModel):
    raw_text: str; line_no: int
    table_number: str | None; table_name: str | None
    action: TableAction
    canonical_id: str | None      # resolved via mapping
    confidence: Literal["CONFIRMED","SUGGESTED","UNRESOLVED"]

class JiraScope(BaseModel):
    ticket_key: str | None        # regex [A-Z][A-Z0-9]+-\d+
    lob: str | None; states: tuple[str, ...]
    filing_effective_date: str | None    # ISO
    renewal_effective_date: str | None
    transaction_scope: tuple[str, ...]   # NB / RENEWAL / ENDORSEMENT, if stated
    edition: str | None
    tables: tuple[JiraTableRef, ...]
    unparsed_signals: tuple[str, ...]    # lines that mention tables/dates but weren't understood
```
Rules (all patterns live in `config/jira_patterns.yaml`, not in code):
- **Action keywords by line or section heading:** `DO NOT LOAD`, `BYPASS`, `IGNORE`, `NO CHANGE`, `N/A` → BYPASS; `REMOVE`, `DELETE`, `RETIRE` → REMOVE; `NEW TABLE`, `ADD` → ADD; `REPLACE`, `FULL RELOAD` → REPLACE; `UPDATE`, `MODIFY`, `CHANGE`, `REVISE` → MODIFY; any table mention inside `~~ ~~` → BYPASS. Section headings ("Tables to update:", "Bypass:") set the action for the list beneath them until the next heading.
- **Conflict:** a table referenced with two different actions → `NEEDS_REVIEW` (it's common for comments to override the description; the **latest comment wins only if** the input includes comment timestamps; otherwise NEEDS_REVIEW).
- **States:** 2-letter codes validated against the US list, plus full state names. LOB: configurable synonyms (`BOP`, `BP7`, `Businessowners`, `CA`, `Commercial Auto`, `Farm`, `FO`, ...). Beware that `CA` is both California and Commercial Auto: resolve by context words ("state", "jurisdiction" vs "line"); if unresolved → `NEEDS_REVIEW`.
- **Dates:** labeled patterns (`Filing Effective Date`, `Effective Date`, `NB Effective`, `Renewal Effective`, `Eff`), parsed by 5.6. Multiple different filing dates → `NEEDS_REVIEW`.
- `--lob/--state` flags override the parsed values and are recorded as overrides.
- **Optional local LLM assist** (`jira.llm.enabled: false` by default): only allowed via a local Ollama endpoint on `127.0.0.1`. It may only *suggest* extra `JiraTableRef`s, shown as `SUGGESTED` and requiring user confirmation; it never sets dates or verdicts. If enabled, a startup check MUST refuse any non-loopback URL.

---

## 8. Reconciliation engines

### 8.1 Engine A: cell-level factor comparison (approved I1 vs export I3)
For each mapped table pair, index both sides: `dict[CompositeKey, Row]`. **Never compare by row position.**

For each key in the approved table:
- Missing in export → `MISSING_ROW_IN_EXPORT` (HIGH).
- Present → for each FACTOR column (matched by canonical name; unmatched factor columns → `NEEDS_MAPPING`):

```
a = approved Decimal, e = export Decimal
s = export column scale (from GW DataType/metadata; else config tables.<id>.scale; else default_scale = 4)
a_r = a.quantize(10^-s, ROUND_HALF_UP)

if a == e                         → MATCH
elif a_r == e                     → MATCH_ROUNDED (INFO)  [records a, a_r, e]
                                     + if |a - a_r| / |a| > precision_materiality (default 0.0005 = 0.05%)
                                       → PRECISION_TRUNCATION (WARNING)
elif e != 0 and a != 0 and (max(|a|,|e|) / min(|a|,|e|)) >= magnitude_ratio (default 5)
                                  → ORDER_OF_MAGNITUDE (CRITICAL)
elif digits(a_r) and digits(e) are a transposition of each other
                                  → TRANSPOSITION (CRITICAL)
elif a and e differ in sign       → SIGN_FLIP (CRITICAL)
else                              → VALUE_MISMATCH (CRITICAL), delta = e - a, pct = (e - a) / a
```
- Wildcard handling: approved WILDCARD vs export concrete value (or the reverse) in a KEY column → `WILDCARD_MISMATCH` (CRITICAL). A FACTOR that is BLANK in the export but a value in the approved → `BLANK_FACTOR` (CRITICAL).
- A per-table `abs_tolerance` override is CONFIGURABLE but defaults to **0**, and any non-zero override is printed in the report header.

### 8.2 Engine B: baseline delta (baseline vs I3), runs only when a baseline exists (`--baseline` or Snapshot History)
For every table in the export (mapped to the baseline by the same algorithm; baseline ↔ export mapping is usually exact by table code):
- Compute the changed key set `Δ = {added, removed, value_changed}`.
- **Classify each table:**
  - `CHANGED_DECLARED`: Δ ≠ ∅ and in Jira scope (ADD/MODIFY/REPLACE/REMOVE) → OK.
  - `CHANGED_UNDECLARED`: Δ ≠ ∅ and not in Jira scope and not in the approved file → `UNDECLARED_CHANGE` (CRITICAL).
  - `UNCHANGED_DECLARED`: Δ = ∅ but in Jira scope as ADD/MODIFY/REPLACE → `MISSING_UPDATE` (CRITICAL).
  - `BYPASS_VIOLATION`: Δ ≠ ∅ and Jira says BYPASS → CRITICAL.
  - `REMOVE_NOT_APPLIED`: Jira says REMOVE and the table still exists unchanged → HIGH.
- Approved-vs-baseline cross-check: approved rows equal to baseline values (i.e. "approved says no change") are reported as INFO `APPROVED_ROW_UNCHANGED`, which helps reviewers spot stale approved files.

### 8.3 Engine C: scope audit (Jira vs detected)
Produces the scope matrix: every canonical table that appears in Jira, approved, export or baseline, with columns `in_jira(action)`, `in_approved`, `in_export`, `changed_vs_baseline`, `status`. Additional rules:
- Jira lists a table (non-bypass) that isn't in the approved file → `APPROVED_FILE_MISSING_TABLE` (HIGH).
- The approved file has a table that isn't in Jira → `UNDOCUMENTED_APPROVED_TABLE` (WARNING).
- An approved table not found in the export → `TABLE_MISSING_IN_EXPORT` (CRITICAL).

### 8.4 Engine D: range integrity (run on approved and export separately, and compared)
For tables with RANGE_MIN/RANGE_MAX, group rows by all other KEY columns, sort by min, then check:
- INTEGER, inclusive-inclusive: `next.min == prev.max + 1`.
- DECIMAL / inclusive-exclusive: `next.min == prev.max`.
- Convention is per table (config or learned); default INCLUSIVE_INCLUSIVE for INTEGER, INCLUSIVE_EXCLUSIVE for DECIMAL. Unknown convention + both patterns present → `NEEDS_REVIEW`.
- Report `RANGE_GAP`, `RANGE_OVERLAP` (HIGH), `RANGE_MIN_GT_MAX` (CRITICAL), `OPEN_END_MISSING` (WARNING: the last band has no max and isn't open-ended by convention).
- **Boundary shift:** a band present in both approved and export with the same factor but min/max differing by exactly 1 → `OFF_BY_ONE_BOUNDARY` (CRITICAL). This is reported in addition to the row mismatch, so the root cause is clear.

### 8.5 Engine E: metadata / edition / date audit
- Export `effective_date` (and per-table effective date if present) == Jira `filing_effective_date` → else `EFFECTIVE_DATE_MISMATCH` (CRITICAL).
- Export `jurisdiction`/`lob` (if present in metadata) consistent with Jira → else `SCOPE_METADATA_MISMATCH` (HIGH).
- Export edition differs from baseline edition when there are declared changes → else `EDITION_NOT_INCREMENTED` (WARNING, configurable to HIGH).
- Approved file metadata (if a META/info sheet exists) is compared the same way (WARNING level).
- Status values (e.g. Draft/Active/Approved) are reported as-is, with no judgement unless configured.

### 8.7 Snapshot History (the tool's own baseline)
Because teams normally have no "before" export, the tool keeps a local history of exports it has already seen.

- **Identity:** a rate book is identified by `(rate_book_code, jurisdiction, lob)` from export metadata (fallback: user-confirmed name, stored as a learned alias).
- **What is stored per run, per table** (in the local data dir, Section 9.2, `snapshots.db`): canonical table id, GW table code, edition/effective date, row count, a SHA-256 of the canonically ordered normalized rows, and the **normalized rows themselves** (compressed JSON) so a later run can show cell-level differences. This data never leaves the machine. `snapshots.retention_runs` (default 20 per rate book) and `rgl snapshot purge` control retention.
- **Snapshot status:** `VERIFIED` when the run's verdict was PASS / PASS_WITH_WARNINGS / PASS_WITH_LIMITATIONS **and** the user confirms "this export is the accepted state" (CLI prompt at the end of `rgl run`, or a UI button; `--accept-snapshot` for scripts). Otherwise `UNVERIFIED`. Only VERIFIED snapshots are used as a baseline.
- **Use:** if no `--baseline` is given, Engine B uses the latest VERIFIED snapshot of the same rate book whose edition or effective date is **older** than the current filing. The report names it ("Baseline: snapshot from run R-…, accepted by <user> on <date>, edition …").
- **Fast path:** tables whose hash equals the snapshot hash are UNCHANGED without a row diff, which keeps 200-table books fast.
- **Bootstrap tip (documented):** a QA analyst can run `rgl snapshot take --export <file>` on a QA/Prod export **before** a developer loads changes. This creates a baseline at zero cost.
- `rgl snapshot list|show|take|accept|purge` commands.

### 8.6 Drift mode (`rgl drift --left A.xlsx --right B.xlsx`)
Export vs. export (e.g. SAASQA vs GWUpdate vs Prod). Reuses mapping plus Engine A logic with both sides as exports (exact equality, no rounding allowance). Output: tables identical / differing / only-left / only-right, plus a cell-level diff.

---

## 9. Feedback & learning (local, user-confirmed, never silent)

### 9.1 What can be learned (allowlist; nothing else)
| Kind | Example | Effect |
|---|---|---|
| `TABLE_ALIAS` | `"1 BLD Base Rate"` ↔ `"BOP_BLD_BaseRate_Ext"` → `BOP:1` | Mapping becomes CONFIRMED |
| `COLUMN_SYNONYM` | `"Terr"` → `territory` | Normalization |
| `COLUMN_ROLE` | table `BOP:121`, column `stories_from` = RANGE_MIN | Role inference |
| `WILDCARD_TOKEN` | table `BOP:42`, column `class_code`: `-1` = wildcard | Normalization |
| `LAYOUT_FINGERPRINT` | header/type-row positions + metadata labels for a GW export layout | Header detection |
| `RANGE_CONVENTION` | `BOP:121` INCLUSIVE_INCLUSIVE | Engine D |
| `JIRA_PATTERN` | phrase `"no rate change"` → BYPASS | Parser |
| `FINDING_DISPOSITION` | a specific finding confirmed as true defect / false positive with reason | Metrics + future triage hint only |

**Hard rule (MUST):** learning can change *how inputs are read and mapped*. It can **never** change comparison math, tolerances, severities, or turn a FAIL into a PASS. A `FINDING_DISPOSITION = FALSE_POSITIVE` is shown as a hint ("previously marked false positive by X on date: reason") next to an identical future finding, but the finding still counts.

### 9.2 Store
- SQLite file at `%LOCALAPPDATA%\RateGuardLite\learning.db` (Windows) / `~/.local/share/rateguard-lite/learning.db` (Linux) / `~/Library/Application Support/RateGuardLite/learning.db` (macOS); overridable by `RGL_DATA_DIR`.
- Tables: `learned_item(id, kind, scope_lob, scope_table, key, value_json, status[PROPOSED|CONFIRMED|RETIRED], created_by, created_at, confirmed_by, confirmed_at, source_run_id, evidence_json, version)`, `run(id, started_at, tool_version, config_hash, input_hashes_json, verdict, metrics_json)`, `finding_disposition(...)`.
- Append-only versioning: an edit creates a new version and retires the old one. Full history is retained.
- **No input file content is stored**, only names, hashes, header strings, column names, and the specific values needed for a learned item (e.g. a wildcard token).

### 9.3 Flow
1. A run produces `NEEDS_MAPPING` / `SUGGESTED` items → written to `out/proposals.json`.
2. The user reviews them via `rgl review out/` (interactive CLI) or the UI Review tab: accept / edit / reject each one. `created_by`/`confirmed_by` come from the OS username (`getpass.getuser()`), overridable with `--user`.
3. Accepted items → `CONFIRMED`. The user re-runs (or the UI re-runs automatically); the new run records which learned items it used, with their versions, in the run manifest.
4. **Team sharing:** `rgl learn export --out team_mappings.yaml` and `rgl learn import team_mappings.yaml` (import → PROPOSED, needing local confirmation unless `--trust`). Teams can keep `team_mappings.yaml` in an internal Git repo.
5. `rgl learn list|show|retire|diff` for administration.

### 9.4 Learning metrics (shown in `rgl stats`)
Auto-mapped table rate (CONFIRMED / total), suggestion acceptance rate, false-positive rate by finding type, and median manual mappings per run over time. These prove the tool is improving.

---

## 10. Smart test-scenario generator

Output is a **test script for QA to run in PolicyCenter**. The tool does not execute quotes.

### 10.1 Data-driven quote parameters
For each table with confirmed changes (from Engine B Δ if a baseline exists, else the approved rows of declared tables):
- One test per changed key (cap `tests.max_per_table`, default 25; prioritized by |pct change| descending, then first/last bands).
- For range tables: for each changed band `[min, max]`, emit tests at `min`, `max`, `min-1`, and `max+1` (INTEGER) or at `min`, a midpoint, and `max − smallest_step` (DECIMAL). Label each with the expected factor of the band that value falls into, **computed from the approved table**.
- Wildcard rows: one test with a key value **not** explicitly listed, expecting the wildcard factor.
- Out-of-bounds: one test above the highest band → expected "highest band factor or validation error" (configurable expectation).
- Each test lists: `test_id`, `priority (P1–P3)`, `objective`, `table`, `key parameters`, `expected factor`, `source cell (approved file/sheet/cell)`, `what to check in the rating worksheet`.

Columns map to PolicyCenter UI field names via an optional `config/field_map.yaml` (e.g. `number_of_stories` → "Number of Stories (Building screen)"). If unmapped, use the canonical column name.

### 10.2 Transaction & date matrix (from Jira dates; rules CONFIGURABLE because rate book selection is carrier-specific)
Default templates (each can be disabled in config):
| ID | Transaction | Policy effective date | Expected rate book |
|---|---|---|---|
| TX-01 | New Business | filing date − 1 day | prior edition |
| TX-02 | New Business | filing date | new edition |
| TX-03 | Renewal | renewal effective date (or filing date) | new edition |
| TX-04 | Renewal | that date − 1 day | prior edition |
| TX-05 | Policy change (mid-term) on a term that incepted before the filing date | endorsement date ≥ filing date | **per config `midterm_ratebook_rule`** (default: "rate book of the term inception"); report as an assumption to verify |
| TX-06 | Out-of-sequence endorsement on a pre-filing term | — | same as TX-05 |
| TX-07 | Non-premium-bearing policy change (e.g. address correction) | — | **premium change must be $0** |
| TX-08 | Flat cancellation | — | all premium and fees reversed exactly once |

TX-07 and TX-08 are included because the org's incident history shows them as recurring defects. They are QA checks, not computed by this tool.

### 10.3 Output
`test_scenarios.xlsx` (sheets: `Parameter Tests`, `Transaction Matrix`, `Assumptions`) and `test_scenarios.csv`. Deterministic ordering, stable IDs (hash of table + key + objective), so re-runs produce comparable scripts.

---

## 11. Guardrails (all MUST)

1. **Offline:** at process start, install a socket guard that raises on any outbound connection except loopback (`127.0.0.1`/`::1`, only if the local LLM or the Streamlit server needs it). Unit test: any attempted external connection fails the test.
2. **Streamlit:** launched only through `rgl ui`, which sets `--server.address 127.0.0.1`, `--server.headless true`, `--browser.gatherUsageStats false`, `--server.maxUploadSize` = `limits.max_file_mb`, plus a `.streamlit/config.toml` with the same settings. Uploaded files are processed in a per-session temp directory, deleted on session end and at startup (stale sweep).
3. **Read-only inputs:** never write to input paths. Open files read-only. Outputs go only to `--out`.
4. **No code execution from inputs:** no `eval`/`exec`, no VBA, no external links, no formula evaluation. A repository test greps the source for `eval(`, `exec(`, `pickle`, `subprocess` (allowlisted in `rgl ui` only), `os.system`.
5. **Fail closed:** any ambiguity → `NEEDS_MAPPING`/`NEEDS_REVIEW`/`NOT_EVALUATED`, which caps the verdict (Section 12). No code path may produce PASS with unresolved items.
6. **Determinism:** same inputs + config + learned-store version → byte-identical JSON report (excluding timestamp and run_id fields). Test it.
7. **Evidence:** every finding carries file name, sheet, cell coordinate(s), raw values, normalized values and the rule ID that fired.
8. **Data minimization in logs:** log files (`out/run.log`) contain stages, counts, table IDs and rule IDs. They contain factor values only at DEBUG level, which is off by default. Logs never include absolute user paths (use file names) unless `--debug`.
9. **Limits** (Section 5.1) are enforced and configurable only within hard maximums (file ≤ 500 MB, cells ≤ 10 M).
10. **Config validation:** all YAML loaded with `yaml.safe_load`, validated by pydantic models; an unknown key = startup error with a clear message.
11. **Exit codes:** `0` PASS, `1` FAIL, `2` BLOCKED/NEEDS_REVIEW, `3` unsafe/invalid input, `4` internal error, `5` PASS_WITH_WARNINGS/LIMITATIONS (so CI scripts can gate on it).
12. **Error UX:** every error message says *what* failed, *where* (file/sheet/cell), and *what to do next*. No raw stack traces unless `--debug`.
13. **Report HTML:** self-contained (inline CSS, no external fonts/scripts/CDNs), Jinja autoescape on, values never inserted as raw HTML.
14. **Version stamping:** every report includes tool version, config hash, learned-store version used, and SHA-256 of every input.

---

## 12. Verdict logic

Severities: `CRITICAL`, `HIGH`, `WARNING`, `INFO`. Statuses: `NEEDS_MAPPING`, `NEEDS_REVIEW`, `NOT_EVALUATED`.

```
if any unsafe/invalid input                         → BLOCKED   (exit 3)
elif any NEEDS_MAPPING for an in-scope table
     or any table-level BLOCKER (duplicate keys, formula w/o value, Excel error)
                                                    → BLOCKED   (exit 2)
elif any CRITICAL or HIGH finding                   → FAIL      (exit 1)
elif any NEEDS_REVIEW or SUGGESTED (unconfirmed) mapping
                                                    → NEEDS_REVIEW (exit 2)
elif any NOT_EVALUATED (e.g. no baseline)           → PASS_WITH_LIMITATIONS (exit 5)
elif any WARNING                                    → PASS_WITH_WARNINGS (exit 5)
else                                                → PASS      (exit 0)
```
Gate wording in reports: PASS = "Ready for UAT"; FAIL = "Do not promote: fix listed items"; BLOCKED = "Tool could not verify: resolve mappings/input issues"; PASS_WITH_LIMITATIONS = "Values verified; scope checks not performed (no baseline)".

---

## 13. Outputs (in `--out`)

| File | Content |
|---|---|
| `verdict.txt` | Executive summary (format below); also printed to the console with `rich` |
| `reconciliation_report.json` | Full machine-readable result (schema in `schemas/report.schema.json`, generated from pydantic) |
| `reconciliation_report.xlsx` | Sheets: `Summary`, `Discrepancies`, `Scope Audit`, `Range Integrity`, `Metadata Audit`, `Mapping`, `Warnings`, `Inputs` |
| `reconciliation_report.html` | Self-contained, readable by non-technical users, with a filterable discrepancy table (vanilla JS inline) |
| `test_scenarios.xlsx` / `.csv` | Section 10 |
| `jira_comment.txt` | Plain-text summary ready to paste into Jira (no attachments, no URLs) |
| `proposals.json` | Mapping/learning proposals awaiting review |
| `run_manifest.json` | Versions, hashes, config, learned items used, timings |
| `run.log` | Section 11.8 |

`verdict.txt` format:
```
RATEGUARD-LITE VERDICT: FAIL — Do not promote: fix listed items
Ticket: PC-12345 | LOB: BOP | State: OR | Filing effective: 2027-03-15
Rate book: BOPByPeril_OR_RateBook | Export edition: 2025-V1 | Baseline: provided
Tables: 42 analyzed | 39 matched | 2 with discrepancies | 1 scope violation
Cells: 18,450 compared | 3 critical | 1 high | 2 warnings | 4 rounded matches
BLOCKING ITEMS
 [CRITICAL] Table 121 BLD Number of Stories — Stories=4: approved 1.0450 (Approved.xlsx!121 Stories!C6) vs export 1.0250 (−1.91%)
 ...
 [CRITICAL] Table 48 EPLI Coverage — BYPASS_VIOLATION: marked DO NOT LOAD in Jira line 14; 12 rows changed vs baseline
NEXT STEPS
 1. Correct the 3 factors in Table 121 and re-export.
 2. Revert Table 48 to baseline edition.
```
Every finding in every report includes a **plain-language explanation** and a **suggested fix**, generated from per-rule templates in `config/messages.yaml` (no LLM).

---

## 14. Configuration (`config/`)

```
config/
  default.yaml            # tolerances, limits, defaults, feature flags
  profiles/<name>.yaml    # overrides per team/LOB, merged over default
  gw_layouts.yaml         # GW export layout profiles (5.3)
  synonyms.yaml           # column synonyms, abbreviations, LOB synonyms
  wildcards.yaml          # global + per-table/column wildcard tokens
  jira_patterns.yaml      # action keywords, section headings, date labels
  field_map.yaml          # canonical column → PolicyCenter UI field label
  messages.yaml           # finding explanations + suggested fixes
  tests.yaml              # test generator templates, midterm_ratebook_rule
```
`default.yaml` (locked defaults):
```yaml
comparison:
  default_scale: 4
  precision_materiality: 0.0005
  magnitude_ratio: 5
  abs_tolerance: 0          # per-table overrides allowed; printed in report
completeness:
  mode: FULL                # only FULL supported in v1
  small_table_ratio: 0.50
snapshots:
  enabled: true
  retention_runs: 20
dates:
  order: US_MDY
limits:
  max_file_mb: 100
  max_unzipped_mb: 500
  max_cells: 2000000
  max_header_scan_rows: 30
mapping:
  suggest_threshold: 0.80
  suggest_margin: 0.10
approved:
  strikethrough_means: review   # review | delete
jira:
  llm:
    enabled: false
    endpoint: "http://127.0.0.1:11434"
tests:
  max_per_table: 25
```

---

## 15. Repository structure

```
rateguard-lite/
  README.md                   # install, quick start, screenshots, FAQ, data-handling statement
  pyproject.toml
  requirements.lock
  setup.ps1                   # Windows: checks Python ≥3.11, creates .venv, installs, runs self-test
  setup.sh                    # macOS/Linux equivalent
  .streamlit/config.toml
  config/ ...                 # Section 14
  schemas/report.schema.json
  src/rateguard_lite/
    __init__.py               # __version__
    cli.py                    # typer app: run, drift, inspect, map, review, learn, stats, ui, selftest, sample
    guard/                    # socket guard, file safety gate, limits
    ingest/
      workbook.py             # openpyxl loading, inventory, formatting flags
      layout.py               # header/region detection, GW metadata extraction
      normalize.py            # values, wildcards, dates, decimals, leading zeros
      approved.py             # I1 (single, folder, zip)
      gw_export.py            # I3/I4
      jira.py                 # Section 7
    model/                    # pydantic models (Section 3), findings, report
    mapping/                  # table + column matching (Section 6)
    engines/
      factors.py              # Engine A
      baseline.py             # Engine B
      scope.py                # Engine C
      ranges.py               # Engine D
      metadata.py             # Engine E
      drift.py
    snapshots/                # Section 8.7 store, hashing, baseline selection
    tests_gen/                # Section 10
    learning/                 # SQLite store, proposals, review, import/export
    report/                   # verdict, json, xlsx, html (+ templates/), jira comment
    ui/app.py                 # Streamlit (tabs: Run, Review & Learn, History/Stats, Settings)
  tests/
    fixtures/generator.py     # synthetic workbook builder (Section 17)
    unit/ ...
    integration/ ...
    golden/                   # expected JSON reports for fixture scenarios
  docs/
    DECISIONS.md
    USER_GUIDE.md             # for QA/actuaries, no jargon
    ADMIN_GUIDE.md            # config, learning store, team mappings
    DATA_HANDLING.md          # what is read, stored, never sent
    FINDING_CATALOG.md        # every rule ID, severity, meaning, fix
```

CLI commands:
```
rgl run --approved A --jira J --export E [--baseline B] --out DIR [--lob --state --profile --user --overwrite --debug]
rgl drift --left E1 --right E2 --out DIR
rgl inspect FILE          # prints detected sheets, layouts, header rows, columns/roles, metadata. Used to calibrate on a new GW version
rgl map --run DIR         # interactive mapping of NEEDS_MAPPING items
rgl review DIR            # accept/edit/reject proposals.json
rgl learn list|show|retire|export|import|diff
rgl stats
rgl snapshot list|show|take|accept|purge
rgl ui                    # local Streamlit on 127.0.0.1
rgl selftest              # runs the bundled synthetic scenarios and checks expected verdicts
rgl sample --out DIR      # writes synthetic sample inputs for training/demo
```

---

## 16. Build phases (each ends with tests passing)

1. **Skeleton:** repo, pyproject, config loading + validation, CLI stubs, socket guard, file safety gate, logging, exit codes. Tests for the guards.
2. **Fixture generator** (Section 17) and `rgl sample`.
3. **Ingestion:** workbook inventory, layout detection, normalization, approved (single/folder/zip), GW export, `rgl inspect`. Unit tests per normalization rule.
4. **Jira parser** with all patterns from config. Tests on text, Markdown, HTML and wiki-markup fixtures, including strikethrough.
5. **Mapping** (tables + columns) and the completeness rule.
6. **Engines A–E + drift.** Property-based tests (hypothesis): row shuffling never changes results; identical inputs → PASS; any single mutated factor → exactly one CRITICAL finding.
7. **Verdict + reports** (txt/json/xlsx/html/jira comment) + golden tests.
8. **Test-scenario generator.**
9. **Learning store**, proposals, review CLI, import/export, stats.
10. **Streamlit UI.**
11. **Docs, setup scripts, `rgl selftest`, performance check** (Section 18), final hardening.

---

## 17. Synthetic fixtures (we have no real samples yet)

`tests/fixtures/generator.py` MUST produce realistic **synthetic** workbooks (no real carrier data) for a BOP-like LOB in state "OR":
- Approved master workbook with ~8 tabs (e.g. `1 BLD Base Rate`, `21 BPP Base Rate`, `42 GL Class Code Factor`, `48 EPLI Coverage`, `121 BLD Number of Stories`, `10 Vandalism Exclusion`, `Territory Factor`, `Deductible Factor`), business headers in row 1, some yellow-highlighted rows, one strikethrough row.
- GW-style export: **one sheet per rate table, with 50–200+ sheets per workbook** (generator parameter `--tables N`, default 120; scenarios at 50, 120 and 220). Each sheet has a metadata block (Rate Book Code, Edition, Table Code, Effective Date) + a parameter/DataType row + header row, rows shuffled, and internal table codes like `BOP_BLD_BaseRate_Ext`. Most tables are untouched by the change and absent from the approved file.
- **Realistic table mix** in the generated export: ~40% single-key lookups (5–50 rows), ~25% range tables (stories, sq ft, limits, deductibles; 5–40 bands), ~25% two/three-key tables (territory × class, coverage × deductible; 100–5,000 rows), ~10% large tables (territory × class × construction, 10k–30k rows). Include string codes with leading zeros, wildcard rows, DECIMAL factors at scales 3–6, a few INTEGER/BOOLEAN factor columns, and sheet names truncated at Excel's 31-character limit (a real Guidewire export quirk: mapping must not rely on full names).
- Approved workbook: 3–15 whole tables per change.
- Baseline export (prior edition) and a pre-populated snapshot store, for baseline/snapshot scenarios.
- Jira stories: `.txt`, `.md`, `.html`, including a `~~Table 48~~ DO NOT LOAD` bypass.
- **Scenario set**, each with its expected verdict and finding IDs in `tests/golden/`:
  `clean_pass`, `factor_mismatch`, `order_of_magnitude`, `rounded_match_only`, `precision_truncation`, `missing_row`, `extra_row_full_table`, `approved_table_suspiciously_small`, `snapshot_baseline_undeclared_change`, `snapshot_first_run_limitations`, `export_220_tables_perf`, `truncated_sheet_names`, `wildcard_to_blank`, `off_by_one_range`, `range_gap`, `duplicate_key`, `formula_without_cache`, `bypass_violation`, `undeclared_change`, `missing_update_resolved_by_full_table`, `effective_date_mismatch`, `no_baseline_limitations`, `unmappable_table_blocked`, `leading_zero_class_code`, `multi_file_zip_approved`, `drift_two_envs`, `unsafe_zip_traversal`, `encrypted_file`, `xlsm_with_macro_warning`.

**First real-world calibration step (documented in USER_GUIDE):** run `rgl inspect` on one real PolicyCenter export, then adjust `gw_layouts.yaml` or confirm the layout through `rgl map`. Real files stay local and are never added to the repo; `.gitignore` excludes `*.xlsx`, `*.xlsm`, `*.zip` outside `tests/fixtures/generated/` (which is itself generated at test time, not committed).

---

## 18. Acceptance criteria (definition of done)

| # | Criterion | Target |
|---|---|---|
| A1 | All fixture scenarios (Section 17) produce the expected verdict and finding IDs | 100% |
| A2 | Seeded single-cell mutations (hypothesis, ≥ 500 cases) detected | 100%, 0 missed |
| A3 | False positives on `clean_pass` + `rounded_match_only` | 0 CRITICAL/HIGH |
| A4 | Row order invariance | Shuffled inputs → identical report |
| A5 | Determinism | Identical JSON (excluding timestamps/run_id) across 3 runs |
| A6 | Performance (laptop, 16 GB) | 120-table export (~300k cells) + 10 approved tables ≤ 30 s; 220-table export (~600k cells) ≤ 60 s; ≤ 2 GB RAM. Snapshot-hash fast path skips unchanged tables. If needed, load sheets lazily and use openpyxl `read_only=True` for **export** sheets (formatting flags are only required on the approved side) |
| A7 | No network | Socket-guard test passes; Streamlit bound to 127.0.0.1 |
| A8 | Unsafe inputs | All negative fixtures rejected with exit code 3 and a clear message |
| A9 | Learning | After confirming mappings once, re-running the same scenario → 0 NEEDS_MAPPING; learning never changes a FAIL to PASS (test) |
| A10 | Install | Fresh Windows machine with Python 3.11+: `git clone` → `.\setup.ps1` → `rgl selftest` green in ≤ 10 minutes, no admin rights |
| A11 | Quality gates | ruff clean, mypy strict on core, coverage ≥ 85% on core |
| A12 | Docs | USER_GUIDE lets a QA analyst run a reconciliation without developer help |

---

## 19. Known limitations (state them in README; do not hide them)
- Verifies **rate table contents and scope**, not rate routines/algorithms, premium math, or Guidewire rate book *selection logic* (the transaction matrix gives QA tests for that).
- GW export layouts vary by version/configuration; the first run on a new layout requires calibration (`rgl inspect` / `rgl map`).
- On the **first** run of a rate book with no `--baseline`, changes to tables outside the approved file can't be detected (`PASS_WITH_LIMITATIONS`). From the second accepted run on, Snapshot History provides the baseline. Taking a snapshot before development (`rgl snapshot take`) removes this gap.
- Jira parsing is pattern-based; unusual phrasing becomes `NEEDS_REVIEW` until a `JIRA_PATTERN` is learned.
- The approved workbook is treated as truth; RateGuard-Lite does not validate actuarial correctness.
