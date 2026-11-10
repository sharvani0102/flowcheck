"""Facts shared by the checks: dataflow states, writers, reachability, and the
classification of reads that are not defined on any path."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from functools import cached_property
from typing import Dict, List, Optional, Set

from ..dataflow import (FeasibilityOracle, NullOracle, reachable_from, solve, state_has,
                        state_has_exact)
from ..findings import Finding
from ..ir import Node, Read, Workflow
from ..modules import module_internal_writes, resolve_module

SOFT_KINDS = {"cond", "truth", "return"}


def finding(rule: str, severity: str, node: Node, message: str, hint: str = "",
            variable: Optional[str] = None) -> Finding:
    return Finding(rule_id=rule, severity=severity, node_id=node.yaml_path or node.name,
                   message=message, hint=hint, node_name=node.name, node_type=node.type,
                   line=node.line, variable=variable)


@dataclass
class Leak:
    """Why a read that is undefined on every path would otherwise be defined."""

    kind: str  # parallel | sibling | module
    writer: Optional[Node] = None
    module: str = ""


class Analysis:
    def __init__(self, wf: Workflow, oracle: Optional[FeasibilityOracle] = None) -> None:
        self.wf = wf
        self.oracle = oracle or NullOracle()
        self.base: Set[str] = set(wf.parameters) | set(wf.builtins)
        self.may = solve(wf, "may", discard=True, base=self.base)
        self.must = solve(wf, "must", discard=True, base=self.base)
        self.may_nodiscard = solve(wf, "may", discard=False, base=self.base)
        self.writers: Dict[str, List[Node]] = {}
        self.pattern_writers: List[Node] = []
        for n in wf.nodes.values():
            for w in n.writes:
                if w.pattern is None:
                    self.writers.setdefault(w.name, []).append(n)
                else:
                    self.pattern_writers.append(n)

    @cached_property
    def reach(self) -> Dict[int, Set[int]]:
        return {n: reachable_from(self.wf, n) for n in self.wf.nodes}

    def reaches(self, a: int, b: int) -> bool:
        return b in self.reach[a]

    # -- definedness -------------------------------------------------
    def maybe_defined(self, node: Node, name: str) -> bool:
        return name in self.base or state_has(self.may[node.id], name)

    def surely_defined(self, node: Node, name: str) -> bool:
        return name in self.base or state_has_exact(self.must[node.id], name)

    def written_anywhere(self, name: str) -> bool:
        if name in self.writers:
            return True
        import re
        return any(w.pattern and re.match(w.pattern, name)
                   for n in self.pattern_writers for w in n.writes)

    def relevant_reads(self, node: Node) -> List[Read]:
        """Reads the undefined/maybe-undefined rules should look at.

        Skips ``increment`` targets (an undefined value counts as 0,
        variables.py:57-60) and comparison operands that are never written
        anywhere (the runtime treats them as string literals,
        conditions.py:120; semantics §12.4).
        """
        out = []
        seen = set()
        for r in node.reads:
            if r.kind == "increment":
                continue
            if r.kind == "cond" and not self.written_anywhere(r.name) \
                    and r.name not in self.base:
                continue
            key = (r.name, r.field, r.kind)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    # -- undefined reads ---------------------------------------------
    def _discarding_scope(self, writer: Node, reader: Node) -> Optional[int]:
        reader_chain = set(self.wf.scope_chain(reader.scope))
        for s in self.wf.scope_chain(writer.scope):
            if self.wf.scopes[s].discards_writes and s not in reader_chain:
                return s
        return None

    def leak_for(self, node: Node, name: str) -> Optional[Leak]:
        """For a read undefined on every path: is the variable written in a
        scope whose writes don't reach here (FC003)?"""
        writers = self.writers.get(name, [])
        if state_has(self.may_nodiscard[node.id], name):
            for w in writers:
                if self.reaches(w.id, node.id) and self._discarding_scope(w, node) is not None:
                    return Leak("parallel", writer=w)
        reader_chain = self.wf.scope_chain(node.scope)
        for w in writers:
            for s in self.wf.scope_chain(w.scope):
                sc = self.wf.scopes[s]
                if sc.kind != "parallel_branch" or s in reader_chain:
                    continue
                for rs in reader_chain:
                    rsc = self.wf.scopes[rs]
                    if rsc.kind == "parallel_branch" and rsc.fork == sc.fork:
                        return Leak("sibling", writer=w)
        for c in self.wf.nodes.values():
            mods = c.extra.get("modules") or []
            if not mods or not self.reaches(c.id, node.id):
                continue
            for m in mods:
                p = resolve_module(m, self.wf.path)
                if p is not None and name in module_internal_writes(str(p)):
                    return Leak("module", writer=c, module=m)
        return None

    def later_writers(self, node: Node, name: str) -> List[Node]:
        return [w for w in self.writers.get(name, []) if self.reaches(node.id, w.id)]

    def suggestion(self, name: str) -> Optional[str]:
        known = set(self.writers) | self.base
        m = difflib.get_close_matches(name.strip(), sorted(known), n=1, cutoff=0.8)
        return m[0] if m and m[0] != name else None
