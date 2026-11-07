# Finding catalog

Severities: **BLOCKER** (tool cannot verify) · **CRITICAL** · **HIGH** · **WARNING** · **INFO**. Statuses: **NEEDS_MAPPING**, **NEEDS_REVIEW**, **NOT_EVALUATED**.
Verdict impact: BLOCKER/NEEDS_MAPPING → BLOCKED · CRITICAL/HIGH → FAIL · NEEDS_REVIEW → NEEDS_REVIEW · NOT_EVALUATED → PASS_WITH_LIMITATIONS · WARNING → PASS_WITH_WARNINGS.
The plain-language text shown in reports lives in `config/messages.yaml`.

## Factor comparison (Engine A)
| Rule | Level | Meaning | Typical fix |
|---|---|---|---|
| `VALUE_MISMATCH` | CRITICAL | Guidewire factor differs from approved. | Correct the factor, re-export. |
| `ORDER_OF_MAGNITUDE` | CRITICAL | Ratio ≥ 5x (decimal point / unit error). | Check the decimal point. |
| `TRANSPOSITION` | CRITICAL | Same digits, two swapped. | Re-key from the approved file. |
| `SIGN_FLIP` | CRITICAL | Opposite sign. | Correct the sign. |
| `BLANK_FACTOR` | CRITICAL (export) / HIGH (approved) | A factor cell is blank. | Enter the factor. |
| `WILDCARD_MISMATCH` | CRITICAL | A wildcard row is concrete on the other side (or vice versa). | Align the row. |
| `MISSING_ROW_IN_EXPORT` | HIGH | Approved row not in export. | Add the row. |
| `EXTRA_ROW_IN_EXPORT` | HIGH | Export row not in the approved (full) table. | Remove it or confirm the approved file is complete. |
| `MATCH_ROUNDED` | INFO | Equals approved after rounding to the column scale. | None. |
| `PRECISION_TRUNCATION` | WARNING | Rounding changes the value by more than the materiality threshold. | Increase scale or confirm with actuarial. |
| `APPROVED_TABLE_SUSPICIOUSLY_SMALL` | NEEDS_REVIEW | Approved has < 50% of the export's rows. | Confirm it is the whole table / right mapping. |

## Scope (Engines B and C)
| Rule | Level | Meaning |
|---|---|---|
| `UNDECLARED_CHANGE` | CRITICAL | Changed vs baseline; not in Jira or approved. |
| `BYPASS_VIOLATION` | CRITICAL | Jira says bypass/do-not-load but the table changed. |
| `MISSING_UPDATE` | CRITICAL | Declared update, table unchanged, approved differs. |
| `REMOVE_NOT_APPLIED` | HIGH | Jira says remove; table still there, unchanged. |
| `TABLE_MISSING_IN_EXPORT` | CRITICAL | Approved table has no counterpart in the export. |
| `APPROVED_FILE_MISSING_TABLE` | HIGH | Jira declares a table that is not in the approved workbook. |
| `UNDOCUMENTED_APPROVED_TABLE` | WARNING | Approved table not mentioned in Jira. |
| `APPROVED_ROW_UNCHANGED` | INFO | Approved equals baseline (stale approved file?). |
| `NOT_EVALUATED_NO_BASELINE` | NOT_EVALUATED | No baseline; scope checks skipped. |

## Mapping and review
| Rule | Level | Meaning |
|---|---|---|
| `NEEDS_MAPPING` | NEEDS_MAPPING | Table, column or sheet layout could not be matched. Run `rgl map`. |
| `SUGGESTED_MAPPING` | NEEDS_REVIEW | Matched by similarity; confirm it. |
| `JIRA_CONFLICT` / `JIRA_REVIEW` | NEEDS_REVIEW | Conflicting actions, multiple dates, ambiguous LOB. |
| `JIRA_UNPARSED` | NEEDS_REVIEW | A line about tables/dates could not be understood. |
| `LOB_UNRESOLVED` | NEEDS_REVIEW | No line of business in Jira or `--lob`. |
| `STRIKETHROUGH_ROW` | NEEDS_REVIEW / INFO | Struck-through approved row (config `strikethrough_means`). |
| `AMBIGUOUS_DATE`, `UNPARSEABLE_VALUE`, `MERGED_FACTOR` | NEEDS_REVIEW | Cell cannot be read unambiguously. |

## Range integrity (Engine D)
| Rule | Level | Meaning |
|---|---|---|
| `RANGE_GAP` | HIGH | Gap between consecutive bands. |
| `RANGE_OVERLAP` | HIGH | Bands overlap. |
| `RANGE_MIN_GT_MAX` | CRITICAL | A band's min exceeds its max. |
| `OFF_BY_ONE_BOUNDARY` | CRITICAL | A band with the same factor is shifted by exactly one. |
| `OPEN_END_MISSING` | WARNING | Last band has a blank max that is not treated as open-ended. |

## Metadata (Engine E)
| Rule | Level | Meaning |
|---|---|---|
| `EFFECTIVE_DATE_MISMATCH` | CRITICAL (export) / WARNING (approved info sheet) | Date differs from the Jira filing date. |
| `EFFECTIVE_DATE_NOT_EVALUATED` | NOT_EVALUATED | Jira or export has no usable date. |
| `SCOPE_METADATA_MISMATCH` | HIGH | Jurisdiction or LOB differs from Jira. |
| `EDITION_MISMATCH` | HIGH | Edition differs from the one named in Jira. |
| `EDITION_NOT_INCREMENTED` | WARNING (configurable HIGH) | Same edition as the baseline despite changes. |

## Input integrity
| Rule | Level | Meaning |
|---|---|---|
| `DUPLICATE_KEY` | BLOCKER (WARNING for identical export duplicates) | Two rows share a key. |
| `EXCEL_ERROR` | BLOCKER | A cell holds `#REF!`, `#N/A`, ... |
| `FORMULA_NO_VALUE` | BLOCKER | A formula has no cached value. Recalculate and save in Excel. |
| `HIDDEN_ROW`, `HIDDEN_COL`, `MERGED_KEY_FILLED`, `POSSIBLE_LEADING_ZERO_LOSS`, `DATE_HAS_TIME`, `PERCENT_SIGN_IGNORED` | WARNING | Formatting quirks that may hide data. |
| `FORMULA_CACHED`, `TABLE_NOTE`, `VBA_PRESENT`, `INPUT_INFO` | INFO | Informational. |
| `INPUT_WARNING`, `HIDDEN_SHEET` | WARNING | External links/OLE found; hidden sheet not used. |
