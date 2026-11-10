"""FC002 maybe-undefined: some path to the reader writes the variable, but
not every path does (definite assignment: intersection at merge points).
Loops may run zero times (semantics §12.3). A warning, because the path that
skips the write may be infeasible; phase two can discharge such paths with a
FeasibilityOracle.
"""

from __future__ import annotations

from typing import List

from ..dataflow import describe_path, witness_path
from ..findings import WARNING, Finding
from .common import Analysis, finding

RULE = "FC002"


def check(an: Analysis) -> List[Finding]:
    out = []
    wf = an.wf
    for node in wf.nodes.values():
        for r in an.relevant_reads(node):
            if not an.maybe_defined(node, r.name) or an.surely_defined(node, r.name):
                continue
            writers = {w.id for w in an.writers.get(r.name, [])}
            path = witness_path(wf, node.id, writers)
            if path is not None and an.oracle.check(wf, path) is False:
                continue  # phase two: provably infeasible, so no finding
            if not writers and any(w.name == "result_N" for n in an.pattern_writers
                                   for w in n.writes):
                why = ("module-form parallel writes result_1..result_N only for the entries "
                       "parameters_list actually has (calls.py:462-469)")
            elif not writers:
                why = ("it can only come from a templated save_as, whose name isn't known "
                       "statically")
            else:
                decisions = describe_path(wf, path, writers) if path else []
                if decisions:
                    why = "it is not written " + "; ".join(decisions[-2:])
                elif any(wf.scopes[s].kind == "loop_body"
                         for s in wf.scope_chain(node.scope)):
                    why = "it is only written later in the loop, so not on the first iteration"
                else:
                    why = "some path to this step skips every write of it"
            shown = "{{" + r.path + "}}" if r.kind == "template" else f"'{r.path}'"
            out.append(finding(
                RULE, WARNING, node,
                f"{shown} may be undefined here: {why}",
                f"make sure '{r.name}' is written on every path, e.g. initialise it with "
                "set_variable before the branch or loop",
                variable=r.name))
    return out
