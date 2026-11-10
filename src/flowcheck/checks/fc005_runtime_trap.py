"""FC005 runtime-trap: constructs AgentSPEX silently mis-executes or rejects,
found while parsing (unsupported step kinds, a switch without a variable, a
condition outside the supported grammar, missing required fields, ...).
All are certain from the YAML, so errors. This rule is an addition to the
original FC001-FC004 spec; results report it separately.
"""

from __future__ import annotations

from typing import List

from ..findings import ERROR, Finding
from .common import Analysis, finding

RULE = "FC005"


def check(an: Analysis) -> List[Finding]:
    return [finding(RULE, ERROR, an.wf.nodes[t.node], t.message, t.hint)
            for t in an.wf.traps]
