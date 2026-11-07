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
            f"{path.name} is {mb:.0f} MB, which is bigger than the {limits.max_file_mb} MB the tool accepts.",
            where=path.name,
            next_step="Split the workbook into smaller files, or ask your admin to raise limits.max_file_mb (the most allowed is 500).",
        )


def _check_ext(path: Path, allowed: set[str]) -> None:
    if path.suffix.lower() not in allowed:
        raise UnsafeInputError(
            f"We can't use {path.name}: '{path.suffix}' is the wrong file type for this step.",
            where=path.name,
            next_step=f"Save the file as {' or '.join(sorted(allowed))} and run again.",
        )


def _check_zip_members(zf: zipfile.ZipFile, path: Path, limits: LimitsCfg) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise UnsafeInputError(f"{path.name} holds too many files ({len(infos)}; the limit is {MAX_MEMBERS}).", where=path.name,
                               next_step="Use a smaller zip file.")
    total = 0
    for i in infos:
        name = i.filename.replace("\\", "/")
        if name.startswith("/") or posixpath.isabs(name) or ".." in name.split("/") or (len(name) > 1 and name[1] == ":"):
            raise UnsafeInputError(
                f"{path.name} holds a file with an unsafe path inside it ({i.filename!r}).",
                where=path.name,
                next_step="Do not use this file. Ask the sender for a clean copy.",
            )
        total += i.file_size
        if i.compress_size > 0 and i.file_size / i.compress_size > MAX_RATIO:
            raise UnsafeInputError(
                f"{path.name} holds a file ({i.filename!r}) with a suspicious compression ratio, packed far tighter than normal ({MAX_RATIO}:1 or more).",
                where=path.name,
                next_step="Do not open this file. It may be unsafe. Ask the sender for a clean copy.",
            )
    if total > limits.max_unzipped_mb * 1024 * 1024:
        raise UnsafeInputError(
            f"{path.name} would unpack to more than {limits.max_unzipped_mb} MB.", where=path.name,
            next_step="Use a smaller file.",
        )


def safety_gate_workbook(path: Path, limits: LimitsCfg) -> SafetyReport:
    """Validate an .xlsx/.xlsm file. Returns warnings; raises UnsafeInputError if rejected."""
    path = Path(path)
    if not path.is_file():
        raise UnsafeInputError(f"We couldn't find {path.name}.", where=path.name, next_step="Check the file path and run again.")
    _check_ext(path, {".xlsx", ".xlsm"})
    _check_size(path, limits)
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except PermissionError as exc:
        raise UnsafeInputError(f"We couldn't open {path.name} because it's open in Excel or another program, or you don't have permission to read it.",
                               where=path.name, next_step="Close Excel and run again.") from exc
    if head.startswith(OLE_MAGIC):
        raise UnsafeInputError(
            f"We couldn't open {path.name} because it's password-protected (encrypted), or it's an old .xls file.",
            where=path.name,
            next_step="Remove the password in Excel, save a plain .xlsx copy, and run again.",
        )
    if not head.startswith(ZIP_MAGIC):
        raise UnsafeInputError(f"{path.name} doesn't look like an Excel workbook.", where=path.name,
                               next_step="Open it in Excel, save it as .xlsx, and run again.")
    rep = SafetyReport()
    try:
        with zipfile.ZipFile(path) as zf:
            _check_zip_members(zf, path, limits)
            names = zf.namelist()
            if "xl/workbook.xml" not in names:
                raise UnsafeInputError(f"{path.name} doesn't look like an Excel workbook.", where=path.name,
                                       next_step="Open it in Excel, save it as .xlsx, and run again.")
            if any(n.lower().endswith("vbaproject.bin") for n in names):
                rep.info.append(f"{path.name}: contains a VBA project (macros are never loaded or run).")
            if any(n.startswith("xl/externalLinks/") for n in names):
                rep.warnings.append(f"{path.name}: has external links (not followed; cached values are used).")
            if any(n.startswith("xl/embeddings/") for n in names):
                rep.warnings.append(f"{path.name}: has embedded OLE objects (ignored).")
            if "xl/connections.xml" in names:
                rep.warnings.append(f"{path.name}: has data connections (not followed).")
    except zipfile.BadZipFile as exc:
        raise UnsafeInputError(f"{path.name} looks damaged and can't be read.", where=path.name,
                               next_step="Open it in Excel, save a fresh copy as .xlsx, and run again.") from exc
    return rep


def safety_gate_zip(path: Path, limits: LimitsCfg) -> SafetyReport:
    path = Path(path)
    if not path.is_file():
        raise UnsafeInputError(f"We couldn't find {path.name}.", where=path.name, next_step="Check the file path and run again.")
    _check_ext(path, {".zip"})
    _check_size(path, limits)
    with path.open("rb") as fh:
        if not fh.read(4).startswith(ZIP_MAGIC):
            raise UnsafeInputError(f"{path.name} is not a real zip file.", where=path.name, next_step="Create the zip again and run again.")
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
        raise UnsafeInputError(f"{path.name} looks damaged and can't be read.", where=path.name, next_step="Create the zip again and run again.") from exc
    return rep


def safety_gate_text(path: Path, limits: LimitsCfg) -> None:
    path = Path(path)
    if not path.is_file():
        raise UnsafeInputError(f"We couldn't find {path.name}.", where=path.name, next_step="Check the file path and run again.")
    _check_ext(path, {".txt", ".md", ".html", ".htm"})
    _check_size(path, limits)
