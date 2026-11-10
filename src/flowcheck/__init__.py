"""FlowCheck: a static checker for AgentSPEX agent workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .findings import ERROR, WARNING, Finding
from .parser import ParseError, parse_file

__version__ = "0.1.0"


@dataclass
class FileResult:
    path: str
    workflow: Optional[str] = None
    findings: List[Finding] = field(default_factory=list)
    parse_error: Optional[str] = None

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def verdict(self) -> str:
        if self.parse_error:
            return "parse-error"
        if self.errors:
            return "fail"
        if self.warnings:
            return "warn"
        return "pass"


def check_file(path: str, rules: Optional[List[str]] = None) -> FileResult:
    from .checks import run_checks

    try:
        wf = parse_file(path)
    except ParseError as e:
        return FileResult(path=str(path), parse_error=str(e))
    return FileResult(path=str(path), workflow=wf.name, findings=run_checks(wf, rules))


__all__ = ["FileResult", "Finding", "ParseError", "check_file", "parse_file", "__version__"]
