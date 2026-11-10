"""Check registry. Each check takes an ``Analysis`` and returns findings."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ..dataflow import FeasibilityOracle
from ..findings import Finding
from ..ir import Workflow
from . import (fc001_undefined, fc002_maybe_undefined, fc003_scope_leak,
               fc004_isolated_context, fc005_runtime_trap)
from .common import Analysis

CHECKS: Dict[str, Callable[[Analysis], List[Finding]]] = {
    fc001_undefined.RULE: fc001_undefined.check,
    fc002_maybe_undefined.RULE: fc002_maybe_undefined.check,
    fc003_scope_leak.RULE: fc003_scope_leak.check,
    fc004_isolated_context.RULE: fc004_isolated_context.check,
    fc005_runtime_trap.RULE: fc005_runtime_trap.check,
}


def run_checks(wf: Workflow, rules: Optional[List[str]] = None,
               oracle: Optional[FeasibilityOracle] = None) -> List[Finding]:
    an = Analysis(wf, oracle)
    out: List[Finding] = []
    seen = set()
    for rule, fn in CHECKS.items():
        if rules is not None and rule not in rules:
            continue
        for f in fn(an):
            if f.key in seen:
                continue
            seen.add(f.key)
            out.append(f)
    out.sort(key=lambda f: (f.line or 0, f.rule_id, f.node_id, f.variable or ""))
    return out
