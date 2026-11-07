"""Repo-level team mappings (D-024): the primary source of CONFIRMED aliases, column roles and wildcards.

The file (config/team_mappings.yaml, override with RGL_TEAM_MAPPINGS) is plain YAML that the team reviews and
shares through git like any other config. `rgl review` / `rgl map` append confirmed items here with the user and date.
The local SQLite store keeps only PROPOSED items and run history.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ..config import config_dir
from ..errors import ConfigError, RGLError

FORMAT_VERSION = 1
HEADER = (
    "# RateGuard-Lite team mappings - CONFIRMED aliases, column roles and wildcards shared by the whole team.\n"
    "# Written by `rgl review` / `rgl map` (with user and date). Review changes in git like any config file.\n"
    "# Mappings change how inputs are read and matched. They never change comparison math, tolerances or verdicts.\n"
)


def team_file() -> Path:
    env = os.environ.get("RGL_TEAM_MAPPINGS")
    return Path(env) if env else config_dir() / "team_mappings.yaml"


def item_id(kind: str, key: str, scope_lob: str | None, scope_table: str | None) -> str:
    h = hashlib.sha1(f"{kind}|{scope_lob or ''}|{scope_table or ''}|{key}".encode(), usedforsecurity=False).hexdigest()
    return "T-" + h[:6]


def _ident(it: dict[str, Any]) -> tuple[str, str, str, str]:
    return (it["kind"], it.get("scope_lob") or "", it.get("scope_table") or "", it["key"])


def load_items(path: Path | None = None) -> list[dict[str, Any]]:
    from .store import KINDS

    p = path or team_file()
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read the team mappings file: {exc}", where=p.name, next_step="Fix the YAML syntax.") from exc
    if not isinstance(data, dict) or data.get("rateguard_lite_mappings") != FORMAT_VERSION:
        raise ConfigError("Not a RateGuard-Lite team mappings file (missing 'rateguard_lite_mappings: 1').", where=p.name,
                          next_step="Restore the file from git or delete it to start empty.")
    items = data.get("items") or []
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("kind") not in KINDS or "key" not in it or not isinstance(it.get("value"), dict):
            raise ConfigError(f"Invalid entry in the team mappings file: {str(it)[:80]}", where=p.name,
                              next_step=f"Each entry needs kind (one of {', '.join(sorted(KINDS))}), key and value.")
        out.append(it)
    return out


def _write(items: list[dict[str, Any]], path: Path) -> None:
    body = yaml.safe_dump({"rateguard_lite_mappings": FORMAT_VERSION, "items": items}, sort_keys=False, allow_unicode=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(HEADER + body, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        raise RGLError(f"Cannot write the team mappings file: {exc}", where=path.name,
                       next_step="Check the file is not read-only or open in another program.") from exc


def upsert(kind: str, key: str, value: dict[str, Any], *, scope_lob: str | None, scope_table: str | None, user: str,
           source_run_id: str | None = None, path: Path | None = None) -> str:
    """Add or replace one confirmed item. Returns its id."""
    p = path or team_file()
    items = load_items(p)
    new: dict[str, Any] = {"kind": kind, "scope_lob": scope_lob or None, "scope_table": scope_table or None, "key": key, "value": value,
                           "confirmed_by": user, "confirmed_on": date.today().isoformat()}
    if source_run_id:
        new["source_run"] = source_run_id
    ident = _ident(new)
    items = [it for it in items if _ident(it) != ident]
    items.append(new)
    items.sort(key=lambda it: (it["kind"], it.get("scope_lob") or "", it.get("scope_table") or "", it["key"]))
    _write(items, p)
    return item_id(kind, key, scope_lob, scope_table)


def remove(iid: str, path: Path | None = None) -> bool:
    p = path or team_file()
    items = load_items(p)
    keep = [it for it in items if item_id(it["kind"], it["key"], it.get("scope_lob"), it.get("scope_table")) != iid]
    if len(keep) == len(items):
        return False
    _write(keep, p)
    return True


def stamp(path: Path | None = None) -> str:
    p = path or team_file()
    if not p.exists():
        return ""
    items = load_items(p)
    if not items:
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]
