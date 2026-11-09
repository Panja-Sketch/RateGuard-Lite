"""Configuration loading. All YAML via safe_load, validated by pydantic (unknown key = error)."""

from __future__ import annotations

import hashlib
import json
import os
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import ConfigError

HARD_MAX_FILE_MB = 500
HARD_MAX_CELLS = 10_000_000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ComparisonCfg(_Strict):
    default_scale: int = 4
    precision_materiality: float = 0.0005
    magnitude_ratio: float = 5
    abs_tolerance: float = 0
    table_overrides: dict[str, dict[str, float]] = Field(default_factory=dict)


class CompletenessCfg(_Strict):
    mode: Literal["FULL"] = "FULL"
    small_table_ratio: float = 0.5


class SnapshotsCfg(_Strict):
    enabled: bool = False          # D-024: opt-in; the same rate book rarely changes twice
    retention_runs: int = 20


class VerdictCfg(_Strict):
    # D-024: False = a run with no baseline can still PASS (scope check is reported as "not performed").
    require_baseline_for_pass: bool = False


class DatesCfg(_Strict):
    order: Literal["US_MDY", "DMY"] = "US_MDY"


class LimitsCfg(_Strict):
    max_file_mb: int = 100
    max_unzipped_mb: int = 500
    max_cells: int = 2_000_000
    max_header_scan_rows: int = 30


class MappingCfg(_Strict):
    suggest_threshold: float = 0.80
    suggest_margin: float = 0.10


class ApprovedCfg(_Strict):
    strikethrough_means: Literal["review", "delete"] = "review"


class MetadataCfg(_Strict):
    edition_not_incremented_severity: Literal["WARNING", "HIGH"] = "WARNING"


class LlmCfg(_Strict):
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:11434"


class JiraCfg(_Strict):
    llm: LlmCfg = Field(default_factory=LlmCfg)


class ScopeCfg(_Strict):
    # D-032: OUT_OF_SCOPE_TABLE_DATED is off by default (the book-level stamp is shared by most tables, so it is nearly dormant; Engine B covers the baseline case).
    oos_enabled: bool = False
    # D-031: a date/edition stamp carried by more than this share of the export's tables cannot single out an individual table.
    oos_max_stamp_share: float = Field(default=0.30, ge=0.0, le=1.0)


class TestsCfg(_Strict):
    max_per_table: int = 25
    max_must_tests: int = Field(default=8, ge=3, le=20)      # D-026: short 'Must Test' list; hard cap 20


class Config(_Strict):
    comparison: ComparisonCfg = Field(default_factory=ComparisonCfg)
    completeness: CompletenessCfg = Field(default_factory=CompletenessCfg)
    snapshots: SnapshotsCfg = Field(default_factory=SnapshotsCfg)
    verdict: VerdictCfg = Field(default_factory=VerdictCfg)
    dates: DatesCfg = Field(default_factory=DatesCfg)
    limits: LimitsCfg = Field(default_factory=LimitsCfg)
    mapping: MappingCfg = Field(default_factory=MappingCfg)
    approved: ApprovedCfg = Field(default_factory=ApprovedCfg)
    metadata: MetadataCfg = Field(default_factory=MetadataCfg)
    jira: JiraCfg = Field(default_factory=JiraCfg)
    tests: TestsCfg = Field(default_factory=TestsCfg)
    scope: ScopeCfg = Field(default_factory=ScopeCfg)


def config_dir() -> Path:
    """RGL_CONFIG_DIR, else the repo-level config/ folder next to src/."""
    env = os.environ.get("RGL_CONFIG_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "default.yaml").exists():
            return parent / "config"
    raise ConfigError("Cannot find the config folder.", next_step="Set RGL_CONFIG_DIR to the config/ directory.")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read config file: {exc}", where=path.name, next_step="Fix the YAML syntax.") from exc
    return data or {}


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(profile: str = "default") -> Config:
    cdir = config_dir()
    data = _load_yaml(cdir / "default.yaml")
    pfile = cdir / "profiles" / f"{profile}.yaml"
    if profile != "default":
        if not pfile.exists():
            raise ConfigError(f"Profile '{profile}' not found.", where=str(pfile.name), next_step="Check --profile.")
        data = _merge(data, _load_yaml(pfile))
    elif pfile.exists():
        data = _merge(data, _load_yaml(pfile))
    try:
        cfg = Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}",
                          where=f"profile {profile}", next_step="Remove unknown keys or fix values.") from exc
    if cfg.jira.llm.enabled:
        from urllib.parse import urlparse

        host = urlparse(cfg.jira.llm.endpoint).hostname or ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise ConfigError("jira.llm.endpoint must be a loopback address (127.0.0.1) when the local LLM assist is enabled.",
                              where=f"profile {profile}", next_step="Use http://127.0.0.1:<port> or disable jira.llm.")
    if cfg.limits.max_file_mb > HARD_MAX_FILE_MB or cfg.limits.max_cells > HARD_MAX_CELLS:
        raise ConfigError("Limits exceed hard maximums (file <= 500 MB, cells <= 10M).", where="limits")
    return cfg


def config_hash(cfg: Config) -> str:
    blob = json.dumps(cfg.model_dump(), sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


@cache
def aux(name: str) -> dict[str, Any]:
    """Load an auxiliary YAML (synonyms, wildcards, jira_patterns, ...). Cached."""
    return _load_yaml(config_dir() / f"{name}.yaml")


def reset_aux_cache() -> None:
    aux.cache_clear()


def data_dir() -> Path:
    env = os.environ.get("RGL_DATA_DIR")
    if env:
        p = Path(env)
    elif os.name == "nt":
        p = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "RateGuardLite"
    elif os.uname().sysname == "Darwin":  # type: ignore[attr-defined,unused-ignore]
        p = Path.home() / "Library" / "Application Support" / "RateGuardLite"
    else:
        p = Path.home() / ".local" / "share" / "rateguard-lite"
    p.mkdir(parents=True, exist_ok=True)
    return p
