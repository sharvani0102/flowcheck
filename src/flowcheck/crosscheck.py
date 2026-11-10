"""Compare FlowCheck's IR with Lean4Agent's Layer-2 IR JSON files.

Lean4Agent ships two IR flavours (see docs/agentspex-semantics.md §11):

- **ELAIP-Bench** (``elaipbench_layer2/``): produced mechanically by
  ``experiments/elaip_LeanEvolve/build_layer2_ir.py``. It is a flat pre-order
  list of LLM / set_variable / increment nodes, reads taken from the
  ``instruction`` only, a ``branch_path`` tag for nesting, and ``seq`` edges
  between consecutive list entries.
- **SWE-Bench** (``layer2_v2_ir/``): a hand/LLM-authored abstraction with
  typed reads/writes and explicit control nodes and edges.

``compare`` projects FlowCheck's graph into the same shape and returns every
difference as a ``Mismatch``. Nothing is filtered here; the test compares the
result with a reviewed list (``tests/crosscheck/expected_mismatches.yaml``).
"""

from __future__ import annotations

import difflib
import json
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .checks.common import Analysis
from .ir import Node, Workflow
from .parser import parse_file

ELAIP_KEEP = {"step", "task", "set_variable", "increment"}
ELAIP_LEGACY = {"discover", "synthesize"}  # build_layer2_ir.py:247-258, 308-319
SWE_CONTROL = {"conditional": ("if",), "whileLoop": ("while", "for_each"),
               "forEachLoop": ("while", "for_each"), "switchBranch": ("switch",)}


@dataclass(frozen=True)
class Mismatch:
    workflow: str
    kind: str  # node_missing | node_extra | type | reads | writes | branch_path | edge_missing | edge_extra
    node: str
    detail: str

    @property
    def key(self) -> Tuple[str, str, str, str]:
        return (self.workflow, self.kind, self.node, self.detail)

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


# -- pairs of (yaml, ir) in the reference repo -----------------------------

def benchmark_pairs(reference: Path) -> List[Tuple[str, Path, Path]]:
    out = []
    for label, ydir, idir in (
        ("swe", "experiments/SWE_bench_verified", "FormalAgentLib/VerificationExamples/layer2_v2_ir"),
        ("elaip", "experiments/elaipbench", "FormalAgentLib/VerificationExamples/elaipbench_layer2"),
    ):
        for kind in ("passed", "failed"):
            for i in (1, 2, 3):
                stem = f"{kind}_workflow_{i}"
                out.append((f"{label}/{stem}", reference / ydir / f"{stem}.yaml",
                            reference / idir / f"{stem}_layer2.ir.json"))
    return out


# -- helpers ----------------------------------------------------------------

def _diff(ours: Iterable[str], theirs: Iterable[str]) -> Optional[str]:
    a, b = set(ours), set(theirs)
    if a == b:
        return None
    parts = []
    if a - b:
        parts.append("only FlowCheck: " + ", ".join(sorted(a - b)))
    if b - a:
        parts.append("only IR: " + ", ".join(sorted(b - a)))
    return "; ".join(parts)


def project_edges(wf: Workflow, keep: Set[int]) -> Set[Tuple[int, int]]:
    """Edges between kept nodes, collapsing paths through other nodes."""
    out = set()
    for u in keep:
        seen: Set[int] = set()
        todo = deque(e.dst for e in wf.succ_edges(u))
        while todo:
            n = todo.popleft()
            if n in seen:
                continue
            seen.add(n)
            if n in keep:
                out.add((u, n))
                continue
            todo.extend(e.dst for e in wf.succ_edges(n))
    return out


def _reads(an, n: Node) -> Set[str]:
    """FlowCheck's semantic reads: what the checks treat as variable reads
    (comparison literals such as ``== true`` and increment targets excluded,
    semantics §12.4/§12.6)."""
    return {r.name for r in an.relevant_reads(n)}


def _writes(n: Node) -> Set[str]:
    return {w.name for w in n.writes}


def _yaml_name(n: Node) -> str:
    if n.type in ("step", "task"):
        name = n.extra.get("yaml_name")
        return str(name) if name is not None else "<anon>"
    if n.type == "unsupported":
        return n.name.split(":", 1)[1] if ":" in n.name else n.name
    return n.name


def _branch_path(wf: Workflow, n: Node) -> List[str]:
    """FlowCheck's nesting in build_layer2_ir.py's branch_path format."""
    out = []
    for sid in reversed(wf.scope_chain(n.scope)):
        sc = wf.scopes[sid]
        if sc.header is None:
            continue
        h = wf.nodes[sc.header]
        if h.type == "for_each":
            out.append(f"for_each:{h.extra.get('variable') or '?'}")
        elif h.type == "while":
            out.append(f"while:{h.extra.get('condition', '?')[:30]}")
        elif h.type == "if":
            out.append(f"if:{h.extra.get('condition', '?')[:30]}="
                       f"{'true' if sc.arm == 'then' else 'false'}")
        elif h.type == "switch":
            out.append(f"switch:{sc.arm}")
        elif h.type == "fork":
            out.append(f"parallel:{sc.arm}")
    return out


def _align(ours: List[Tuple[str, str]], theirs: List[Tuple[str, str]]
           ) -> Tuple[Dict[int, int], List[int], List[int]]:
    """Order-preserving alignment of (type, name) sequences.

    Returns ours_index -> theirs_index, unmatched ours, unmatched theirs.
    Nodes are matched on name first; a type difference is reported later.
    """
    sm = difflib.SequenceMatcher(a=[n for _, n in ours], b=[n for _, n in theirs],
                                 autojunk=False)
    pairs: Dict[int, int] = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            pairs[blk.a + k] = blk.b + k
    un_o = [i for i in range(len(ours)) if i not in pairs]
    used = set(pairs.values())
    un_t = [j for j in range(len(theirs)) if j not in used]
    return pairs, un_o, un_t


# -- ELAIP ------------------------------------------------------------------

def _compare_elaip(label: str, wf: Workflow, ir: dict) -> List[Mismatch]:
    out: List[Mismatch] = []
    an = Analysis(wf)
    ours = [n for n in sorted(wf.nodes.values(), key=lambda n: n.id)
            if n.type in ELAIP_KEEP or (n.type == "unsupported"
                                        and n.extra.get("kind") in ELAIP_LEGACY)]
    theirs = sorted(ir["nodes"], key=lambda d: d["id"])
    o_keys = [(n.type if n.type != "unsupported" else n.extra["kind"], _yaml_name(n))
              for n in ours]
    t_keys = [(d["step_type"], d["name"]) for d in theirs]
    pairs, un_o, un_t = _align(o_keys, t_keys)

    for i in un_o:
        out.append(Mismatch(label, "node_extra", f"{o_keys[i][1]}", f"FlowCheck {ours[i].yaml_path}"))
    for j in un_t:
        out.append(Mismatch(label, "node_missing", f"{t_keys[j][1]}", f"IR node {theirs[j]['id']}"))

    for i, j in sorted(pairs.items()):
        n, d = ours[i], theirs[j]
        tag = f"{d['id']}:{d['name']}"
        if o_keys[i][0] != d["step_type"]:
            out.append(Mismatch(label, "type", tag, f"FlowCheck {o_keys[i][0]}, IR {d['step_type']}"))
        r = _diff(_reads(an, n), d.get("reads_templates") or [])
        if r:
            out.append(Mismatch(label, "reads", tag, r))
        w = _diff(_writes(n), [d["save_as"]] if d.get("save_as") else [])
        if w:
            out.append(Mismatch(label, "writes", tag, w))
        bp_o, bp_t = _branch_path(wf, n), list(d.get("branch_path") or [])
        if bp_o != bp_t:
            out.append(Mismatch(label, "branch_path", tag,
                                f"FlowCheck {bp_o}, IR {bp_t}"))

    # Edges, in IR ids.
    o2t = {ours[i].id: theirs[j]["id"] for i, j in pairs.items()}
    names = {d["id"]: d["name"] for d in theirs}
    keep = set(o2t)
    ours_e = {(o2t[a], o2t[b]) for a, b in project_edges(wf, keep)}
    theirs_e = {(e["from"], e["to"]) for e in ir["edges"]}
    out += _edge_mismatches(label, ours_e, theirs_e, names)
    return out


def _edge_mismatches(label: str, ours_e: Set[Tuple[int, int]], theirs_e: Set[Tuple[int, int]],
                     names: Dict[int, str]) -> List[Mismatch]:
    out = []
    fmt = lambda e: f"{e[0]}:{names.get(e[0])} -> {e[1]}:{names.get(e[1])}"
    for e in sorted(theirs_e - ours_e):
        out.append(Mismatch(label, "edge_missing", fmt(e), "edge only in IR"))
    for e in sorted(ours_e - theirs_e):
        out.append(Mismatch(label, "edge_extra", fmt(e), "edge only in FlowCheck"))
    return out


# -- SWE --------------------------------------------------------------------

def _swe_edges(ir: dict) -> Set[Tuple[int, int]]:
    out = set()
    for e in ir["edges"]:
        k = e.get("edge_type")
        if k in ("seq", "loopBack"):
            out.add((e["from_node"], e["to_node"]))
        elif k == "branch":
            out.add((e["cond_node"], e["then_entry"]))
            out.add((e["cond_node"], e["else_entry"]))
        elif k == "loop":
            out.add((e["header"], e["body_entry"]))
            out.add((e["header"], e["exit_node"]))
        elif k == "switch":
            for b in e.get("branches", []):
                out.add((e["cond_node"], b))
            if e.get("default_case") is not None:
                out.add((e["cond_node"], e["default_case"]))
        else:
            raise ValueError(f"unknown SWE IR edge type {k!r}")
    return out


def _compare_swe(label: str, wf: Workflow, ir: dict) -> List[Mismatch]:
    out: List[Mismatch] = []
    an = Analysis(wf)
    theirs = sorted(ir["nodes"], key=lambda d: d["id"])
    real = [n for n in sorted(wf.nodes.values(), key=lambda n: n.id)
            if n.type not in ("entry", "exit", "join", "fork", "loop_exit", "bind")]
    t2o: Dict[int, Node] = {}
    used: Set[int] = set()

    # LLM nodes by name, in order.
    for d in theirs:
        if d["step_type"] in SWE_CONTROL:
            continue
        cand = [n for n in real if n.id not in used and n.type in ("step", "task")
                and _yaml_name(n) == d["name"]]
        if cand:
            t2o[d["id"]] = cand[0]
            used.add(cand[0].id)
    # Control nodes: the IR may omit some of ours, so pick, for each IR control
    # node, the compatible FlowCheck header whose successor steps overlap most
    # with the IR node's successors (ties broken by document order).
    # Successors are compared in IR-id space; an unmatched FlowCheck successor
    # counts against the candidate.
    their_edges = _swe_edges(ir)
    for d in theirs:
        if d["step_type"] not in SWE_CONTROL:
            continue
        want = {b for a, b in their_edges if a == d["id"]}
        o2t_now = {n.id: tid for tid, n in t2o.items()}
        best, best_score = None, None
        for n in real:
            if n.type not in SWE_CONTROL[d["step_type"]] or n.id in used:
                continue
            succ = {o2t_now.get(b, ("ours", b)) for a, b in
                    project_edges(wf, set(o2t_now) | {n.id}) if a == n.id}
            score = len(want & succ) - len(succ - want)
            if best_score is None or score > best_score:
                best, best_score = n, score
        if best is not None:
            t2o[d["id"]] = best
            used.add(best.id)

    for d in theirs:
        if d["id"] not in t2o:
            out.append(Mismatch(label, "node_missing", d["name"], f"IR node {d['id']} ({d['step_type']})"))
    for n in real:
        if n.id not in used:
            out.append(Mismatch(label, "node_extra", n.name, f"FlowCheck {n.type} at {n.yaml_path}"))

    swe_types = {"if": "conditional", "while": "whileLoop", "for_each": "forEachLoop",
                 "switch": "switchBranch"}
    for d in theirs:
        n = t2o.get(d["id"])
        if n is None:
            continue
        tag = f"{d['id']}:{d['name']}"
        ot = swe_types.get(n.type, n.type)
        if ot != d["step_type"]:
            out.append(Mismatch(label, "type", tag, f"FlowCheck {ot}, IR {d['step_type']}"))
        r = _diff(_reads(an, n), [x["name"] for x in d.get("reads", [])])
        if r:
            out.append(Mismatch(label, "reads", tag, r))
        w = _diff(_writes(n), [x["name"] for x in d.get("writes", [])])
        if w:
            out.append(Mismatch(label, "writes", tag, w))

    o2t = {n.id: tid for tid, n in t2o.items()}
    names = {d["id"]: d["name"] for d in theirs}
    ours_e = {(o2t[a], o2t[b]) for a, b in project_edges(wf, set(o2t))}
    out += _edge_mismatches(label, ours_e, _swe_edges(ir), names)
    return out


def compare(label: str, yaml_path: Path, ir_path: Path) -> List[Mismatch]:
    wf = parse_file(yaml_path)
    ir = json.loads(Path(ir_path).read_text(encoding="utf-8"))
    is_elaip = any("reads_templates" in d for d in ir.get("nodes", []))
    return (_compare_elaip if is_elaip else _compare_swe)(label, wf, ir)


def compare_all(reference: Path) -> List[Mismatch]:
    out: List[Mismatch] = []
    for label, y, j in benchmark_pairs(reference):
        out += compare(label, y, j)
    return out
