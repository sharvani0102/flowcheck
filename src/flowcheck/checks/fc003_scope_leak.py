"""FC003 scope-leak: the variable is written, but only inside a scope whose
writes never reach the reader: a list-form ``parallel`` child (deep-copied
context, AgentSPEX calls.py:730-745), a sibling parallel branch, or the body
of a called module (isolated context, calls.py:226-250). Branches and loops
are *not* scopes in AgentSPEX (semantics §5), so they never trigger FC003.
"""

from __future__ import annotations

from typing import List

from ..findings import ERROR, Finding
from .common import Analysis, finding

RULE = "FC003"


def check(an: Analysis) -> List[Finding]:
    out = []
    for node in an.wf.nodes.values():
        for r in an.relevant_reads(node):
            if an.maybe_defined(node, r.name):
                continue
            leak = an.leak_for(node, r.name)
            if leak is None:
                continue
            w = leak.writer
            if leak.kind == "parallel":
                msg = (f"'{r.name}' is written by '{w.name}' (line {w.line}) inside a parallel "
                       "block, but each parallel child runs on a throwaway copy of the context, "
                       "so the write is lost after the block (calls.py:730-745)")
                hint = ("move the producing step out of 'parallel:', or use 'gather'/"
                        "module-form 'parallel' with save_as / save_results_as to export it")
            elif leak.kind == "sibling":
                msg = (f"'{r.name}' is written by '{w.name}' (line {w.line}) in a sibling "
                       "parallel branch; parallel children never see each other's writes "
                       "(calls.py:730-733)")
                hint = "run the producer before the parallel block"
            else:
                msg = (f"'{r.name}' is written inside module '{leak.module}' (called at line "
                       f"{w.line}), but a module runs in an isolated context and only its "
                       "returned value comes back, through the call's save_as "
                       "(calls.py:226-250, 342-348)")
                hint = (f"in the call, add 'return: {r.name}' and 'save_as: {r.name}'")
            out.append(finding(RULE, ERROR, node, msg, hint, variable=r.name))
    return out
