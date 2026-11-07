"""Verdict logic (spec 12). Fail closed: no code path yields PASS with unresolved items."""

from __future__ import annotations

from .errors import EXIT_BLOCKED, EXIT_FAIL, EXIT_LIMITED, EXIT_PASS
from .model.findings import Finding, Severity, Status, Verdict

EXIT_BY_VERDICT = {
    Verdict.PASS: EXIT_PASS,
    Verdict.FAIL: EXIT_FAIL,
    Verdict.BLOCKED: EXIT_BLOCKED,
    Verdict.NEEDS_REVIEW: EXIT_BLOCKED,
    Verdict.PASS_WITH_WARNINGS: EXIT_LIMITED,
    Verdict.PASS_WITH_LIMITATIONS: EXIT_LIMITED,
}


def decide(findings: list[Finding]) -> Verdict:
    sev = {f.severity for f in findings if f.severity}
    st = {f.status for f in findings if f.status}
    if Status.NEEDS_MAPPING in st or Severity.BLOCKER in sev:
        return Verdict.BLOCKED
    if Severity.CRITICAL in sev or Severity.HIGH in sev:
        return Verdict.FAIL
    if Status.NEEDS_REVIEW in st:
        return Verdict.NEEDS_REVIEW
    if Status.NOT_EVALUATED in st:
        return Verdict.PASS_WITH_LIMITATIONS
    if Severity.WARNING in sev:
        return Verdict.PASS_WITH_WARNINGS
    return Verdict.PASS
