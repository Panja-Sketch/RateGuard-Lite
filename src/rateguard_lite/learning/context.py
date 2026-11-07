"""Read-only view of confirmed learned items used by a run (spec 9.1).

Learning only changes how inputs are read and mapped - never comparison math or verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LearnedContext:
    # (lob, normalized name key) -> canonical id
    table_aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    column_synonyms: dict[str, str] = field(default_factory=dict)
    # canonical table id -> {canonical column -> ROLE}
    column_roles: dict[str, dict[str, str]] = field(default_factory=dict)
    # canonical table id -> {column -> [tokens]}
    wildcard_tokens: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    range_conventions: dict[str, str] = field(default_factory=dict)
    # extra Jira phrases: phrase -> action
    jira_patterns: dict[str, str] = field(default_factory=dict)
    # sheet name -> True when the user confirmed a hidden sheet mapping
    hidden_sheets_ok: set[str] = field(default_factory=set)
    used: list[dict[str, object]] = field(default_factory=list)  # for the run manifest
    version: str = "0"

    def alias_for(self, lob: str, key: str) -> str | None:
        return self.table_aliases.get((lob, key))
