"""File safety gate (spec 5.1). Runs BEFORE any parser touches a file."""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import LimitsCfg
from ..errors import UnsafeInputError

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0"  # legacy / password-encrypted OOXML container
MAX_RATIO = 100
MAX_MEMBERS = 2000


@dataclass
class SafetyReport:
    """Non-fatal observations (shown as WARNING/INFO in the report)."""

    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)


def _check_size(path: Path, limits: LimitsCfg) -> None:
    mb = path.stat().st_size / (1024 * 1024)
    if mb > limits.max_file_mb:
        raise UnsafeInputError(
            f"File is {mb:.0f} MB, above the {limits.max_file_mb} MB limit.",
            where=path.name,
            next_step="Split the workbook or raise limits.max_file_mb (hard max 500).",
        )


def _check_ext(path: Path, allowed: set[str]) -> None:
    if path.suffix.lower() not in allowed:
        raise UnsafeInputError(
            f"Unsupported file type '{path.suffix}'.",
            where=path.name,
            next_step=f"Use one of: {', '.join(sorted(allowed))}.",
        )


def _check_zip_members(zf: zipfile.ZipFile, path: Path, limits: LimitsCfg) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise UnsafeInputError(f"Archive has {len(infos)} members (limit {MAX_MEMBERS}).", where=path.name)
    total = 0
    for i in infos:
        name = i.filename.replace("\\", "/")
        if name.startswith("/") or posixpath.isabs(name) or ".." in name.split("/") or (len(name) > 1 and name[1] == ":"):
            raise UnsafeInputError(
                f"Archive member has an unsafe path: {i.filename!r}.",
                where=path.name,
                next_step="Do not use this file; ask the sender for a clean copy.",
            )
        total += i.file_size
        if i.compress_size > 0 and i.file_size / i.compress_size > MAX_RATIO:
            raise UnsafeInputError(
                f"Member {i.filename!r} has a suspicious compression ratio (> {MAX_RATIO}:1).",
                where=path.name,
                next_step="This may be a zip bomb; do not open the file.",
            )
    if total > limits.max_unzipped_mb * 1024 * 1024:
        raise UnsafeInputError(
            f"Archive expands to more than {limits.max_unzipped_mb} MB.", where=path.name,
            next_step="Use a smaller file.",
        )


def safety_gate_workbook(path: Path, limits: LimitsCfg) -> SafetyReport:
    """Validate an .xlsx/.xlsm file. Returns warnings; raises UnsafeInputError if rejected."""
    path = Path(path)
    if not path.is_file():
        raise UnsafeInputError("File not found.", where=path.name, next_step="Check the path.")
    _check_ext(path, {".xlsx", ".xlsm"})
    _check_size(path, limits)
    with path.open("rb") as fh:
        head = fh.read(8)
    if head.startswith(OLE_MAGIC):
        raise UnsafeInputError(
            "Workbook is encrypted/password-protected (or an old .xls format).",
            where=path.name,
            next_step="Remove the password in Excel and save a plain .xlsx copy.",
        )
    if not head.startswith(ZIP_MAGIC):
        raise UnsafeInputError("File content is not a valid Excel workbook (bad header).", where=path.name,
                               next_step="Re-save the file from Excel as .xlsx.")
    rep = SafetyReport()
    try:
        with zipfile.ZipFile(path) as zf:
            _check_zip_members(zf, path, limits)
            names = zf.namelist()
            if "xl/workbook.xml" not in names:
                raise UnsafeInputError("Archive is not an Excel workbook (xl/workbook.xml missing).", where=path.name)
            if any(n.lower().endswith("vbaproject.bin") for n in names):
                rep.info.append(f"{path.name}: contains a VBA project (macros are never loaded or run).")
            if any(n.startswith("xl/externalLinks/") for n in names):
                rep.warnings.append(f"{path.name}: has external links (not followed; cached values are used).")
            if any(n.startswith("xl/embeddings/") for n in names):
                rep.warnings.append(f"{path.name}: has embedded OLE objects (ignored).")
            if "xl/connections.xml" in names:
                rep.warnings.append(f"{path.name}: has data connections (not followed).")
    except zipfile.BadZipFile as exc:
        raise UnsafeInputError("Workbook archive is corrupt.", where=path.name, next_step="Re-save from Excel.") from exc
    return rep


def safety_gate_zip(path: Path, limits: LimitsCfg) -> SafetyReport:
    path = Path(path)
    if not path.is_file():
        raise UnsafeInputError("File not found.", where=path.name, next_step="Check the path.")
    _check_ext(path, {".zip"})
    _check_size(path, limits)
    with path.open("rb") as fh:
        if not fh.read(4).startswith(ZIP_MAGIC):
            raise UnsafeInputError("File is not a valid zip archive.", where=path.name)
    rep = SafetyReport()
    try:
        with zipfile.ZipFile(path) as zf:
            _check_zip_members(zf, path, limits)
            for n in zf.namelist():
                if n.endswith("/"):
                    continue
                if not n.lower().endswith((".xlsx", ".xlsm")):
                    rep.warnings.append(f"{path.name}: ignored non-workbook member '{n}'.")
    except zipfile.BadZipFile as exc:
        raise UnsafeInputError("Zip archive is corrupt.", where=path.name) from exc
    return rep


def safety_gate_text(path: Path, limits: LimitsCfg) -> None:
    path = Path(path)
    if not path.is_file():
        raise UnsafeInputError("File not found.", where=path.name, next_step="Check the path.")
    _check_ext(path, {".txt", ".md", ".html", ".htm"})
    _check_size(path, limits)
