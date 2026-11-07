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


def extract_number(name: str, strict: bool = False) -> str | None:
    """Table number from a name. strict=True ignores the weak '_123_' pattern (used for identity)."""
    for pat in _NUM_PATTERNS[:2] if strict else _NUM_PATTERNS:
        m = pat.search(name or "")
        if m:
            return str(int(m.group(1)))
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


def canonical_id(lob: str, *names: str) -> str:
    """'{LOB}:{number}' if a table number is extractable from any name, else '{LOB}:{slug}'."""
    for n in names:
        num = extract_number(n, strict=True)
        if num:
            return f"{lob}:{num}"
    for n in names:
        if n:
            return f"{lob}:{slug(n)}"
    return f"{lob}:UNNAMED"


def name_key(name: str) -> str:
    """Normalized key used by the alias store."""
    return " ".join(sorted(expand_tokens(name))) or (name or "").strip().upper()
