"""Deterministic Jira story parser (spec 7). All patterns live in config/jira_patterns.yaml."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

from bs4 import BeautifulSoup
from bs4.element import NavigableString
from pydantic import BaseModel, ConfigDict

from ..config import Config, aux
from ..guard.safety import safety_gate_text
from ..learning.context import LearnedContext
from .normalize import AmbiguousDateError, parse_date

US_STATES = {
    "AL": "ALABAMA", "AK": "ALASKA", "AZ": "ARIZONA", "AR": "ARKANSAS", "CA": "CALIFORNIA", "CO": "COLORADO",
    "CT": "CONNECTICUT", "DE": "DELAWARE", "FL": "FLORIDA", "GA": "GEORGIA", "HI": "HAWAII", "ID": "IDAHO",
    "IL": "ILLINOIS", "IN": "INDIANA", "IA": "IOWA", "KS": "KANSAS", "KY": "KENTUCKY", "LA": "LOUISIANA",
    "ME": "MAINE", "MD": "MARYLAND", "MA": "MASSACHUSETTS", "MI": "MICHIGAN", "MN": "MINNESOTA",
    "MS": "MISSISSIPPI", "MO": "MISSOURI", "MT": "MONTANA", "NE": "NEBRASKA", "NV": "NEVADA",
    "NH": "NEW HAMPSHIRE", "NJ": "NEW JERSEY", "NM": "NEW MEXICO", "NY": "NEW YORK", "NC": "NORTH CAROLINA",
    "ND": "NORTH DAKOTA", "OH": "OHIO", "OK": "OKLAHOMA", "OR": "OREGON", "PA": "PENNSYLVANIA",
    "RI": "RHODE ISLAND", "SC": "SOUTH CAROLINA", "SD": "SOUTH DAKOTA", "TN": "TENNESSEE", "TX": "TEXAS",
    "UT": "UTAH", "VT": "VERMONT", "VA": "VIRGINIA", "WA": "WASHINGTON", "WV": "WEST VIRGINIA",
    "WI": "WISCONSIN", "WY": "WYOMING", "DC": "DISTRICT OF COLUMBIA",
}
_NAME_TO_CODE = {v: k for k, v in US_STATES.items()}


class TableAction(StrEnum):
    ADD = "ADD"
    MODIFY = "MODIFY"
    REPLACE = "REPLACE"
    REMOVE = "REMOVE"
    BYPASS = "BYPASS"
    MENTION = "MENTION"


class JiraTableRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_text: str
    line_no: int
    table_number: str | None = None
    table_name: str | None = None
    action: TableAction
    canonical_id: str | None = None
    confidence: Literal["CONFIRMED", "SUGGESTED", "UNRESOLVED"] = "UNRESOLVED"


class JiraScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_key: str | None = None
    lob: str | None = None
    states: tuple[str, ...] = ()
    filing_effective_date: str | None = None
    renewal_effective_date: str | None = None
    transaction_scope: tuple[str, ...] = ()
    edition: str | None = None
    tables: tuple[JiraTableRef, ...] = ()
    unparsed_signals: tuple[str, ...] = ()
    review_flags: tuple[str, ...] = ()      # conflicts / ambiguity (decision D-005)
    overrides: tuple[str, ...] = ()


# ---------------------------------------------------------------- text conversion
def to_plain_text(raw: str, suffix: str) -> str:
    if suffix in (".html", ".htm") or re.search(r"<(html|body|p|div|table|br)\b", raw[:5000], re.I):
        return _html_to_text(raw)
    text = raw.replace("\r\n", "\n").replace("**", "")
    # Jira wiki strikethrough: -text- (not inside words / dates / bullets)
    text = re.sub(r"(?<![\w-])-(?=\S)([^-\n]*?\S)-(?![\w-])", r"~~\1~~", text)
    return text


def _html_to_text(raw: str) -> str:
    soup = BeautifulSoup(raw, "lxml")
    for t in soup(["script", "style", "head"]):
        t.decompose()
    for t in soup.find_all(["del", "s", "strike"]):
        t.insert_before(NavigableString("~~"))
        t.insert_after(NavigableString("~~"))
    for t in soup.find_all(style=re.compile(r"line-through", re.I)):
        t.insert_before(NavigableString("~~"))
        t.insert_after(NavigableString("~~"))
    for t in soup.find_all("li"):
        t.insert(0, NavigableString("- "))
    for t in soup.find_all(["td", "th"]):
        t.append(NavigableString(" | "))
    for t in soup.find_all(["br"]):
        t.replace_with(NavigableString("\n"))
    for t in soup.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"]):
        t.append(NavigableString("\n"))
    text = soup.get_text()
    text = re.sub(r"[ \t]*\|[ \t]*\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text)


# ---------------------------------------------------------------- helpers
_DATE_RE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})"
_STRIKE_RE = re.compile(r"~~(.+?)~~")


def _kw_regex(words: list[str]) -> re.Pattern[str]:
    ws = sorted(words, key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in ws) + r")(?![A-Za-z0-9])", re.I)


def _clean_line(s: str) -> str:
    s = re.sub(r"^\s*(?:#{1,6}\s*|h[1-6]\.\s*)", "", s)
    s = re.sub(r"^[\s\-\*•·]+(?=\S)", "", s)
    s = re.sub(r"^\d+[.)]\s+(?=table\b)", "", s, flags=re.I)
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"^\*([^*]+)\*$", r"\1", s.strip())
    return s.strip()


def _heading_key(line: str) -> str:
    return re.sub(r"[\s:*#_]+$", "", re.sub(r"^[\s#*_]+", "", re.sub(r"^h[1-6]\.\s*", "", line.strip(), flags=re.I))).upper()


def _try_date(s: str, order: str) -> str | None:
    try:
        return parse_date(s, order)[0]
    except AmbiguousDateError:
        return None


# ---------------------------------------------------------------- main parse
def parse_jira_text(text: str, cfg: Config, learned: LearnedContext | None = None) -> JiraScope:
    pat = aux("jira_patterns")
    syn = aux("synonyms")
    order = cfg.dates.order
    actions: dict[str, list[str]] = {k: list(v) for k, v in pat["actions"].items()}
    if learned:
        for phrase, act in learned.jira_patterns.items():
            actions.setdefault(act, []).append(phrase)
    kw = {a: _kw_regex(w) for a, w in actions.items()}
    precedence = ["BYPASS", "REMOVE", "REPLACE", "ADD", "MODIFY"]
    headings: dict[str, str] = {k.upper(): v for k, v in pat["headings"].items()}
    lines = text.split("\n")

    flags: list[str] = []
    unparsed: list[str] = []

    # ---- ticket key
    m = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", text)
    ticket = m.group(1) if m else None

    # ---- dates
    filing: dict[str, int] = {}
    renewal: dict[str, int] = {}
    dated_lines: set[int] = set()
    rlabels = sorted(pat["date_labels"]["renewal"], key=len, reverse=True)
    flabels = sorted(pat["date_labels"]["filing"], key=len, reverse=True)

    def find_dates(line: str, labels: list[str]) -> tuple[list[str], str]:
        found: list[str] = []
        rest = line
        for lab in labels:
            rx = re.compile(r"(?<![A-Za-z])" + re.escape(lab).replace(r"\ ", r"\s+") + r"[^0-9A-Za-z]{0,5}(?:(?:is|on|of|date)\s*[^0-9A-Za-z]{0,5})?" + _DATE_RE, re.I)
            for mm in rx.finditer(rest):
                iso = _try_date(mm.group(1), order)
                if iso:
                    found.append(iso)
                else:
                    unparsed.append(f"AMBIGUOUS_DATE: {mm.group(1)}")
            rest = rx.sub(" ", rest)
        return found, rest

    for i, ln in enumerate(lines, 1):
        r_dates, rest = find_dates(ln, rlabels)
        f_dates, rest = find_dates(rest, flabels)
        for d in r_dates:
            renewal[d] = i
        for d in f_dates:
            filing[d] = i
        if r_dates or f_dates:
            dated_lines.add(i)
        elif re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2}\b(?!\d)", ln) and re.search(r"effective|eff\b", ln, re.I):
            unparsed.append(f"AMBIGUOUS_DATE (2-digit year) on line {i}: {ln.strip()[:80]}")
    f_iso = sorted(filing)
    if len(f_iso) > 1:
        flags.append(f"MULTIPLE_FILING_DATES: {', '.join(f_iso)}")
    filing_date = f_iso[0] if len(f_iso) == 1 else None
    r_iso = sorted(renewal)
    if len(r_iso) > 1:
        flags.append(f"MULTIPLE_RENEWAL_DATES: {', '.join(r_iso)}")
    renewal_date = r_iso[0] if len(r_iso) == 1 else None

    # ---- states
    states: list[str] = []
    for mm in re.finditer(r"(?:\b(?:states?|jurisdictions?)\b|filing\s+in|for\s+the\s+state\s+of)\s*[:\-]?\s*((?:[A-Za-z]{2,}(?:[ \t]+[A-Za-z]+)?)(?:[ \t]*[,/&][ \t]*[A-Za-z]{2,}(?:[ \t]+[A-Za-z]+)?)*)", text, re.I):
        for part in re.split(r"[ 	]*[,/&][ 	]*", mm.group(1)):
            p = part.strip().upper()
            code = p if p in US_STATES else _NAME_TO_CODE.get(p)
            if code and code not in states:
                states.append(code)
    up = text.upper()
    for sname, code in _NAME_TO_CODE.items():
        if re.search(r"(?<![A-Z])" + re.escape(sname) + r"(?![A-Z])", up) and code not in states:
            states.append(code)

    # ---- LOB
    lob = None
    lm = re.search(r"\b(?:LOB|line\s+of\s+business|line|product)\b\s*[:\-]\s*([A-Za-z0-9 ]{2,30})", text, re.I)
    syns: dict[str, list[str]] = syn.get("lob_synonyms", {})
    if lm:
        val = lm.group(1).strip().upper()
        for code, words in syns.items():
            if val == code or val in [w.upper() for w in words] or any(val.startswith(w.upper()) for w in words):
                lob = code
                break
        if lob is None and val.split()[0] in syns:
            lob = val.split()[0]
    if lob is None:
        hits: dict[str, int] = {}
        for code, words in syns.items():
            for w in words:
                n = len(re.findall(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", text, re.I))
                if n:
                    hits[code] = hits.get(code, 0) + n
        if hits:
            top = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))
            lob = top[0][0]
            if len(top) > 1 and top[0][1] == top[1][1]:
                flags.append(f"LOB_AMBIGUOUS: {', '.join(k for k, _ in top)}")
                lob = None
    if re.search(r"\bCA\b", text) and lob is None and "CA" not in states:
        unparsed.append("'CA' is ambiguous (California vs Commercial Auto); state it with 'State:' or 'Line:'.")

    # ---- edition & transactions
    edition = None
    em = re.search(r"(?:rate\s*book\s+)?edition\s*[:\-=]?\s*([A-Za-z0-9._\-]+)", text, re.I)
    if em:
        edition = em.group(1)
    tx: list[str] = []
    for key, words in pat["transaction_words"].items():
        rx = _kw_regex(list(words))
        for ln in lines:
            if rx.search(ln) and re.search(r"appl|transaction|scope|effective", ln, re.I):
                tx.append(key)
                break

    # ---- tables
    refs: list[JiraTableRef] = []
    section: str | None = None
    for i, raw_line in enumerate(lines, 1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        hk = _heading_key(stripped)
        if hk in headings:
            section = headings[hk]
            continue
        if stripped.endswith(":") and not re.match(r"^\s*[-*•·]", raw_line) and len(stripped.split()) <= 6:
            section = None          # an unrecognised heading ends the current section
            continue
        line = _clean_line(stripped)
        if not line:
            continue
        is_bullet = bool(re.match(r"^\s*[-*•·]\s+", raw_line))
        # inline "Heading: item, item"
        inline_head = re.match(r"^([A-Za-z ]{4,40}):\s+(.+)$", line)
        if inline_head and _heading_key(inline_head.group(1)) in headings:
            section = headings[_heading_key(inline_head.group(1))]
            line = inline_head.group(2)
        # HTML table row?
        cells = [c.strip() for c in line.split("|") if c.strip()] if "|" in line else []
        number: str | None = None
        name: str | None = None
        body = line
        if cells and re.fullmatch(r"(?:table\s*#?\s*)?\d{1,4}", cells[0], re.I) and len(cells) >= 2:
            number = str(int(re.sub(r"\D", "", cells[0])))
            name = cells[1]
        else:
            nm = re.match(r"^(?:~~)?(?:table\s*#?\s*)?(\d{1,4})(?:~~)?\s*[-–:.)]?\s*(.*)$", line, re.I)
            tm = re.search(r"\btable\s*#?\s*(\d{1,4})\b\s*[-–:]?\s*(.*)", line, re.I)
            if nm and (len(line.split()) > 1 or is_bullet or section) and not re.match(r"^\d{1,2}[/-]\d", line):
                number, name = str(int(nm.group(1))), nm.group(2)
            elif tm:
                number, name = str(int(tm.group(1))), tm.group(2)
        if number is None:
            if section and (is_bullet or len(line.split()) <= 8) and not _DATE_RE_SEARCH(line):
                name = line
            elif re.search(r"\btables?\b", line, re.I) and i not in dated_lines and not re.search(r"\btables?\s+to\b|\bthe following tables?\b", line, re.I):
                unparsed.append(f"line {i}: {line[:90]}")
                continue
            else:
                continue
        # decide action
        struck = bool(_STRIKE_RE.search(line)) and number is not None and bool(re.search(r"~~[^~]*" + re.escape(number) + r"[^~]*~~", line))
        found_actions = [a for a in precedence if kw[a].search(body if not struck else line)]
        if struck:
            action = TableAction.BYPASS
        elif found_actions:
            action = TableAction(found_actions[0])
        elif section:
            action = TableAction(section)
        else:
            action = TableAction.MENTION
        clean_name = None
        if name:
            nm2 = _STRIKE_RE.sub(r"\1", name)
            for a in precedence:
                nm2 = kw[a].sub(" ", nm2)
            nm2 = re.sub(r"\(([^)]*)\)", r" \1 ", nm2)
            nm2 = re.sub(r"[\-–:|~]+", " ", nm2)
            nm2 = re.sub(r"\s+", " ", nm2).strip(" .,;")
            clean_name = nm2 or None
        refs.append(JiraTableRef(raw_text=raw_line.strip()[:200], line_no=i, table_number=number, table_name=clean_name, action=action))

    # ---- conflicts
    by_tbl: dict[str, set[str]] = {}
    for r in refs:
        key = r.table_number or (r.table_name or "").upper()
        if r.action != TableAction.MENTION:
            by_tbl.setdefault(key, set()).add(r.action.value)
    for key, acts in sorted(by_tbl.items()):
        if len(acts) > 1:
            flags.append(f"JIRA_CONFLICT: table {key} has conflicting actions {sorted(acts)}")

    return JiraScope(
        ticket_key=ticket, lob=lob, states=tuple(states), filing_effective_date=filing_date,
        renewal_effective_date=renewal_date, transaction_scope=tuple(dict.fromkeys(tx)), edition=edition,
        tables=tuple(refs), unparsed_signals=tuple(dict.fromkeys(unparsed)), review_flags=tuple(flags),
    )


def _DATE_RE_SEARCH(s: str) -> bool:  # noqa: N802
    return bool(re.search(_DATE_RE, s))


def parse_jira(path: Path, cfg: Config, learned: LearnedContext | None = None, lob: str | None = None, state: str | None = None) -> JiraScope:
    path = Path(path)
    safety_gate_text(path, cfg.limits)
    raw = path.read_text(encoding="utf-8", errors="replace")
    scope = parse_jira_text(to_plain_text(raw, path.suffix.lower()), cfg, learned)
    upd: dict[str, object] = {}
    ov: list[str] = []
    if lob:
        upd["lob"] = lob.upper()
        ov.append(f"lob={lob.upper()} (was {scope.lob})")
    if state:
        upd["states"] = (state.upper(),)
        ov.append(f"state={state.upper()} (was {','.join(scope.states) or 'none'})")
    if ov:
        upd["overrides"] = tuple(ov)
        return scope.model_copy(update=upd)
    return scope
