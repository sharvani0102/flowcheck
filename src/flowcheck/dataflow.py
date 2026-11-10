"""Forward dataflow over the workflow CFG, plus the path-feasibility hook.

Three analyses share one solver:

- **may**: a name is in the set if *some* path from entry writes it (union).
- **must**: a name is in the set if *every* path writes it (intersection,
  starting from TOP). This is classic definite assignment.
- **may_nodiscard**: like *may*, but writes made inside list-form ``parallel``
  children are *not* discarded at the join. Comparing it with *may* tells us
  when a variable only "exists" inside a discarded scope (FC003).

The state at a node is the set of names defined *before* the node runs.
Templated writes (``save_as: a_{{x}}``) appear in *may* states as pattern
tokens and never in *must* states.

Phase-two hook: checks that find a "maybe undefined" read produce a witness
path (a list of edges from the entry to the reader that avoids every writer)
and ask a ``FeasibilityOracle`` about it. Phase one ships ``NullOracle``,
which never knows, so every finding is kept. A Z3-backed oracle can encode
the branch conditions along the path and return ``False`` when the path is
infeasible (for example a ``while recheck_count < 3`` that directly follows
``recheck_count = 0``), letting the check drop the finding.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Dict, FrozenSet, Iterable, List, Optional, Protocol, Set

from .ir import Edge, EdgeKind, Workflow

PATTERN_PREFIX = "\x00re:"
State = Optional[FrozenSet[str]]  # None means TOP (only in the must analysis)


def _node_defs(wf: Workflow, nid: int, with_patterns: bool) -> Set[str]:
    out = set()
    for w in wf.nodes[nid].writes:
        if w.pattern is None:
            out.add(w.name)
        elif with_patterns:
            out.add(PATTERN_PREFIX + w.pattern)
    return out


def solve(wf: Workflow, mode: str, discard: bool = True,
          base: Iterable[str] = ()) -> Dict[int, State]:
    """Return the IN state of every node.

    ``mode`` is ``"may"`` (union) or ``"must"`` (intersection).
    """
    must = mode == "must"
    base_set = frozenset(base)
    defs = {n: frozenset(_node_defs(wf, n, with_patterns=not must)) for n in wf.nodes}
    ins: Dict[int, State] = {}
    outs: Dict[int, State] = {}
    for n in wf.nodes:
        ins[n] = None if must else frozenset()
        outs[n] = None if must else frozenset()
    ins[wf.entry] = base_set

    def out_of(n: int, in_state: State) -> State:
        node = wf.nodes[n]
        fork = node.extra.get("fork") if node.type == "join" else None
        if discard and fork is not None:
            # Parallel children run on deep copies that are thrown away
            # (AgentSPEX calls.py:730-745): the join sees the fork's state.
            return outs[fork]
        if in_state is None:
            return None
        return in_state | defs[n]

    order = sorted(wf.nodes)
    changed = True
    while changed:
        changed = False
        for n in order:
            if n == wf.entry:
                new_in: State = base_set
            else:
                preds = [outs[e.src] for e in wf.pred_edges(n)]
                if must:
                    known = [p for p in preds if p is not None]
                    if not preds:
                        new_in = frozenset()  # unreachable node: nothing defined
                    elif not known:
                        new_in = None
                    else:
                        acc = set(known[0])
                        for p in known[1:]:
                            acc &= p
                        new_in = frozenset(acc)
                else:
                    acc = set()
                    for p in preds:
                        acc |= p or frozenset()
                    new_in = frozenset(acc)
            new_out = out_of(n, new_in)
            if new_in != ins[n] or new_out != outs[n]:
                ins[n], outs[n] = new_in, new_out
                changed = True
    return ins


def state_has(state: State, name: str) -> bool:
    """Is ``name`` defined (exactly or via a write pattern) in ``state``?"""
    if state is None:
        return True
    if name in state:
        return True
    for tok in state:
        if tok.startswith(PATTERN_PREFIX) and re.match(tok[len(PATTERN_PREFIX):], name):
            return True
    return False


def state_has_exact(state: State, name: str) -> bool:
    return state is None or name in state


def reachable_from(wf: Workflow, start: int) -> Set[int]:
    """Nodes reachable from ``start`` by following at least one edge."""
    seen: Set[int] = set()
    todo = deque(e.dst for e in wf.succ_edges(start))
    while todo:
        n = todo.popleft()
        if n in seen:
            continue
        seen.add(n)
        todo.extend(e.dst for e in wf.succ_edges(n))
    return seen


def witness_path(wf: Workflow, target: int, avoid: Set[int]) -> Optional[List[Edge]]:
    """Shortest edge path entry -> target that passes through no node in
    ``avoid`` (target itself excluded). ``None`` if there is none."""
    prev: Dict[int, Edge] = {}
    seen = {wf.entry}
    todo = deque([wf.entry])
    while todo:
        n = todo.popleft()
        if n == target:
            path = []
            while n != wf.entry:
                e = prev[n]
                path.append(e)
                n = e.src
            return list(reversed(path))
        for e in wf.succ_edges(n):
            if e.dst in seen or (e.dst in avoid and e.dst != target):
                continue
            seen.add(e.dst)
            prev[e.dst] = e
            todo.append(e.dst)
    return None


def describe_path(wf: Workflow, path: List[Edge], writers: Set[int] = frozenset()
                  ) -> List[str]:
    """Human-readable decisions along a witness path.

    With ``writers`` given, only decisions that matter are kept: those where
    a different outgoing edge of the same header leads to a writer while the
    taken edge does not.
    """
    entered = {e.src for e in path if e.kind == EdgeKind.BRANCH_TRUE}
    writer_scopes = [set(wf.scope_chain(wf.nodes[w].scope)) for w in writers]

    def writes_in(scope: Optional[int]) -> bool:
        return scope is not None and any(scope in ch for ch in writer_scopes)

    def relevant(e: Edge) -> bool:
        """A decision matters if it skips a region that contains a write."""
        if not writers:
            return True
        h = wf.nodes[e.src]
        if h.type in ("while", "for_each"):
            return writes_in(h.extra.get("body_scope"))
        arms = h.extra.get("arm_scopes", {})
        taken = arms.get(e.label if e.kind == EdgeKind.BRANCH_TRUE else
                         ("else" if h.type == "if" else "default"))
        others = [s for lbl, s in arms.items() if s != taken]
        return not writes_in(taken) and any(writes_in(s) for s in others)

    out = []
    for e in path:
        src = wf.nodes[e.src]
        if src.type in ("if", "switch", "while", "for_each") and not relevant(e):
            continue
        if src.type == "if":
            cond = src.extra.get("condition", "")
            truth = "true" if e.kind == EdgeKind.BRANCH_TRUE else "false"
            out.append(f"when `{cond}` is {truth} (line {src.line})")
        elif src.type == "switch":
            var = src.extra.get("variable")
            label = e.label if e.kind == EdgeKind.BRANCH_TRUE else "default"
            out.append(f"when switch `{var}` takes case `{label}` (line {src.line})")
        elif src.type == "while" and e.kind == EdgeKind.BRANCH_FALSE and e.src not in entered:
            out.append(f"when `while {src.extra.get('condition', '')}` runs zero times "
                       f"(line {src.line})")
        elif src.type == "for_each" and e.kind == EdgeKind.BRANCH_FALSE and e.src not in entered:
            out.append(f"when `for_each` over `{src.extra.get('in')}` has no items "
                       f"(line {src.line})")
    return out


class FeasibilityOracle(Protocol):
    def check(self, wf: Workflow, path: List[Edge]) -> Optional[bool]:
        """True: feasible. False: provably infeasible. None: unknown."""


class NullOracle:
    """Phase-one oracle: knows nothing, so no finding is ever discharged."""

    def check(self, wf: Workflow, path: List[Edge]) -> Optional[bool]:
        return None
