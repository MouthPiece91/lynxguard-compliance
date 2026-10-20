"""Core types shared by every regime: citations, findings, rules and the runner.

Design rules:
- A rule is deterministic: same input + same parameter version -> same findings.
- Every finding carries the citation a human should read to verify it.
- The engine never decides to block a trade. It reports; a person decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import IntEnum
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel


class Severity(IntEnum):
    """Ordered so the worst finding decides the overall outcome."""

    INFO = 0
    REVIEW = 1  # a person must look; the engine cannot decide from the data given
    VIOLATION = 2


class Outcome:
    PASS = "PASS"
    REVIEW = "REVIEW"
    VIOLATION = "VIOLATION"


class Citation(BaseModel):
    source: str  # e.g. "NFA Compliance Rule"
    section: str  # e.g. "2-43(b)"
    url: str


class Finding(BaseModel):
    rule_id: str
    severity: str  # Severity name, kept as text so audit records stay readable
    message: str
    citations: list[Citation]
    parameter_version: str | None = None
    evidence: dict[str, Any] = {}


class UnverifiedParameters(LookupError):
    """Raised when no verified parameter version covers the date being checked."""


E = TypeVar("E")


@dataclass(frozen=True)
class Rule(Generic[E]):
    rule_id: str
    title: str
    citations: tuple[Citation, ...]
    event_type: str
    evaluate: Callable[[E, "ParameterSet"], list[Finding]]
    parameter_group: str | None = None
    version: str = "1"


@dataclass
class ParameterSet:
    """Resolves the parameter version in force on a given date."""

    groups: dict[str, list[dict[str, Any]]]

    def resolve(self, group: str, as_of: date) -> dict[str, Any]:
        versions = sorted(self.groups.get(group, []), key=lambda v: v["in_force_from"])
        chosen = None
        for v in versions:
            if v["in_force_from"] <= as_of:
                chosen = v
        if chosen is None:
            earliest = versions[0]["in_force_from"] if versions else None
            raise UnverifiedParameters(
                f"No verified '{group}' parameters for {as_of}. "
                f"Earliest verified version starts {earliest}."
            )
        return chosen


def severity_outcome(findings: list[Finding]) -> str:
    if not findings:
        return Outcome.PASS
    worst = max(Severity[f.severity] for f in findings)
    return {
        Severity.INFO: Outcome.PASS,
        Severity.REVIEW: Outcome.REVIEW,
        Severity.VIOLATION: Outcome.VIOLATION,
    }[worst]


@dataclass
class CheckResult:
    event_type: str
    outcome: str
    findings: list[Finding]
    rules_run: list[dict[str, str]] = field(default_factory=list)


def run_rules(event_type: str, event: Any, rules: list[Rule], params: ParameterSet) -> CheckResult:
    findings: list[Finding] = []
    rules_run: list[dict[str, str]] = []
    for rule in rules:
        if rule.event_type != event_type:
            continue
        try:
            out = rule.evaluate(event, params)
        except UnverifiedParameters as exc:
            out = [
                Finding(
                    rule_id=rule.rule_id,
                    severity=Severity.REVIEW.name,
                    message=f"Rule could not run: {exc}",
                    citations=list(rule.citations),
                )
            ]
        findings.extend(out)
        rules_run.append({"rule_id": rule.rule_id, "rule_version": rule.version})
    return CheckResult(event_type, severity_outcome(findings), findings, rules_run)
