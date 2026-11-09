"""Table identity helpers (spec 6.1, 6.2): number extraction, token expansion, canonical ids."""

from __future__ import annotations

import re

from ..config import aux

_NUM_PATTERNS = (
    re.compile(r"^\s*(?:table\s*)?#?(\d{1,4})\b", re.I),
    re.compile(r"\btable\s*#?\s*(\d{1,4})\b", re.I),
    re.compile(r"_(\d{1,4})_"),
)
STOPWORDS = {"TABLE", "THE", "OF", "FOR", "AND"}

# D-029: noise that people add to approved tab names ("TAB 12 - Rates", "Copy of 12 Rates", "Rates (2)", "Rates FINAL v2 3-15-27").
_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{8}\b")
_PAREN_COPY_RE = re.compile(r"\(\s*\d{1,2}\s*\)")
_PAREN_WORD_RE = re.compile(r"\(\s*(?:final|draft|copy|rev\w*|new|v\d+(?:\.\d+)?)\s*\)", re.I)
_COPY_RE = re.compile(r"[-_\s]*\bcopy(?:\s+of)?\b[-_\s]*", re.I)
_VERSION_RE = re.compile(r"\b(?:final|draft|revised|rev\s*\d*|v\d+(?:\.\d+)?|version\s*\d+)\b", re.I)
_LEAD_WORD_RE = re.compile(r"^\s*(?:tab|sheet|table|tbl)\s*#?\s*(?=\d)", re.I)
_EDGE_RE = re.compile(r"^[\s\-_\u2013\u2014:|.#]+|[\s\-_\u2013\u2014:|.]+$")


def clean_name(name: str) -> str:
    """Strip prefixes/suffixes people add to a tab name, so the table number and the real name stand out."""
    s = (name or "").replace("_", " ")
    s = _PAREN_COPY_RE.sub(" ", s)
    s = _PAREN_WORD_RE.sub(" ", s)
    s = _DATE_RE.sub(" ", s)
    s = _COPY_RE.sub(" ", s)
    s = _VERSION_RE.sub(" ", s)
    s = _LEAD_WORD_RE.sub("", s.strip())
    s = re.sub(r"\s+", " ", s)
    return _EDGE_RE.sub("", s).strip() or (name or "").strip()


def extract_number(name: str, strict: bool = False, anywhere: bool = False) -> str | None:
    """Table number from a name. strict=True ignores the weak '_123_' pattern (used for identity).

    anywhere=True (approved tab names, D-029) cleans the name first and then accepts the one standalone number found
    anywhere in it; two different numbers are ambiguous and give None.
    """
    for pat in _NUM_PATTERNS[:2] if strict else _NUM_PATTERNS:
        m = pat.search(name or "")
        if m:
            return str(int(m.group(1)))
    if anywhere:
        c = clean_name(name)
        for pat in _NUM_PATTERNS[:2]:
            m = pat.search(c)
            if m:
                return str(int(m.group(1)))
        nums = {str(int(x)) for x in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", c)}
        if len(nums) == 1:
            return next(iter(nums))
    return None


def _split_words(name: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name or "")      # camelCase -> words
    s = re.sub(r"[^A-Za-z0-9]+", " ", s)
    return [w for w in s.upper().split() if w]


def expand_tokens(name: str, keep_digits: bool = False) -> list[str]:
    abbr: dict[str, str] = {k.upper(): v for k, v in aux("synonyms").get("abbreviations", {}).items()}
    out: list[str] = []
    for w in _split_words(name):
        if (w.isdigit() and not keep_digits) or w in STOPWORDS:
            continue
        rep = abbr.get(w, w)
        out.extend(x for x in rep.upper().replace("_", " ").split() if x)
    return out


def slug(name: str) -> str:
    return "_".join(expand_tokens(name, keep_digits=True)) or re.sub(r"\W+", "_", name or "").strip("_").upper() or "UNNAMED"


def canonical_id(lob: str, *names: str, anywhere: bool = False) -> str:
    """'{LOB}:{number}' if a table number is extractable from any name, else '{LOB}:{slug}'."""
    for n in names:
        num = extract_number(n, strict=True, anywhere=anywhere)
        if num:
            return f"{lob}:{num}"
    for n in names:
        if n:
            return f"{lob}:{slug(n)}"
    return f"{lob}:UNNAMED"


def name_key(name: str) -> str:
    """Normalized key used by the alias store."""
    return " ".join(sorted(expand_tokens(name))) or (name or "").strip().upper()
