"""Build small in-memory RateTables for engine tests (no Excel involved)."""

from rateguard_lite.config import load_config
from rateguard_lite.mapping.tables import TablePair
from rateguard_lite.model.core import CellValue, ColumnRole, ColumnSpec, ColumnType, RateTable, Row, SourceRef

CFG = load_config()


def cell(v, coord="A1", kind="VALUE"):
    return CellValue(kind, None if kind != "VALUE" else str(v), None if v is None else str(v), coord)


def make_table(rows, origin="APPROVED", keys=("territory",), factor="factor", scale=4, name="T"):
    cols = []
    for k in keys:
        role = ColumnRole.RANGE_MIN if k.endswith("_from") else ColumnRole.RANGE_MAX if k.endswith("_to") else ColumnRole.KEY
        typ = ColumnType.INTEGER if role != ColumnRole.KEY else ColumnType.STRING
        cols.append(ColumnSpec(k, k, role, typ))
    cols.append(ColumnSpec(factor, factor, ColumnRole.FACTOR, ColumnType.DECIMAL, scale if origin != "APPROVED" else None))
    trs = []
    for i, r in enumerate(rows):
        key = tuple(cell(v, f"{chr(65 + j)}{i + 2}", "WILDCARD" if v == "*" else "VALUE") for j, v in enumerate(r[:-1]))
        trs.append(Row(key, {factor: cell(r[-1], f"{chr(65 + len(keys))}{i + 2}")}, i + 2))
    return RateTable("T:1", name, origin, SourceRef("f.xlsx", "S", "A1:B2", "0" * 64), tuple(cols), tuple(trs), {}, "FULL")


def pair(a, e):
    return TablePair("T:1", "T", a, e, None, "CONFIRMED")
