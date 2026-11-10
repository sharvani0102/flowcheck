"""Mutation testing: inject known bugs into correct workflows.

Each mutation type plants one bug class and records which rule should report
it. A mutant is only created when the expected rule is known from the
workflow's structure (who writes what, where, and who reads it), never from
FlowCheck's own output, so recall is a real measurement.

Mutation types:

- ``delete-producer`` (FC001): delete the only step that writes a variable a
  later step reads with ``{{...}}``.
- ``rename-read`` (FC001): rename one ``{{var}}`` in an instruction to a name
  nothing writes.
- ``remove-branch-write`` (FC002): for an ``if``/``switch`` whose every arm
  writes a variable that is read after it (and not defined before it), remove
  the write from one arm.
- ``move-reader-before-writer`` (FC001): move a top-level reader to just
  before the only (top-level) step that writes what it reads.
- ``parallel-no-export`` (FC003): wrap the only producer of a variable in a
  list-form ``parallel:`` block, which discards its write. (None of the
  passed benchmark workflows contain parallel/gather, so there is no
  existing export to drop; this is the closest equivalent.)
- ``step-to-task`` (FC004): turn a ``step`` that relies on conversation
  history into a ``task``. Selected structurally: the step has an earlier
  ``step`` in its history and receives no earlier output through
  ``{{...}}``, so history is its only source of earlier work. Whether FC004
  then fires depends on its phrase heuristic, which is what this measures.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

import yaml

from .checks.common import Analysis
from .checks.fc004_isolated_context import _isolated, _upstream_producers
from .ir import Node, Workflow

_TOKEN = re.compile(r"^(?P<key>[^\[\]]+)(?:\[(?P<idx>\d+)\])?$")
RENAMED_SUFFIX = "__mutated"


@dataclass
class Mutant:
    kind: str
    expect_rule: str
    expect_var: Optional[str]  # for FC004 the node name is used instead
    expect_node: Optional[str]
    description: str
    data: Dict[str, Any]

    def text(self) -> str:
        return yaml.safe_dump(self.data, sort_keys=False, allow_unicode=True)


# -- locating steps in the raw YAML -----------------------------------------

def _locate(data: Dict[str, Any], node: Node) -> Optional[Tuple[list, int]]:
    """(list, index) holding the YAML step for ``node``."""
    if "." not in node.yaml_path:
        return None
    parent = node.yaml_path.rsplit(".", 1)[0]
    cur: Any = data
    tokens = parent.split(".")
    for t_i, tok in enumerate(tokens):
        m = _TOKEN.match(tok)
        if not m:
            return None
        key, idx = m.group("key"), m.group("idx")
        if not isinstance(cur, dict) or key not in cur:
            return None
        nxt = cur[key]
        if idx is None:
            cur = nxt
            continue
        if not isinstance(nxt, list) or int(idx) >= len(nxt):
            return None
        if t_i == len(tokens) - 1:
            return nxt, int(idx)
        cur = nxt[int(idx)]
    return None


def _body(data: Dict[str, Any], node: Node) -> Optional[Dict[str, Any]]:
    loc = _locate(data, node)
    if loc is None:
        return None
    step = loc[0][loc[1]]
    body = step.get(node.type) if isinstance(step, dict) else None
    return body if isinstance(body, dict) else None


def _top_index(node: Node) -> Optional[int]:
    m = re.fullmatch(r"workflow\[(\d+)\]\.\w+", node.yaml_path)
    return int(m.group(1)) if m else None


# -- facts about the original ----------------------------------------------

def _single_writer(an: Analysis, var: str) -> Optional[Node]:
    ws = [w for w in an.writers.get(var, []) if w.type != "bind"]
    if len(ws) != 1 or an.pattern_writers and any(
            w.pattern and re.match(w.pattern, var) for n in an.pattern_writers for w in n.writes):
        return None
    w = ws[0]
    if w.type not in ("step", "task", "set_variable"):
        return None
    return w


def _template_readers(an: Analysis, var: str) -> List[Node]:
    return [n for n in an.wf.nodes.values()
            if any(r.name == var and r.kind == "template" and r.field == "instruction"
                   for r in n.reads)]


def _defined_vars(an: Analysis) -> List[str]:
    return sorted(v for v in an.writers if v not in an.base)


# -- mutation generators ----------------------------------------------------

def _delete_producer(an: Analysis, data: dict) -> Iterator[Mutant]:
    for var in _defined_vars(an):
        w = _single_writer(an, var)
        if w is None:
            continue
        readers = [r for r in _template_readers(an, var) if an.reaches(w.id, r.id)]
        if not readers:
            continue
        m = copy.deepcopy(data)
        loc = _locate(m, w)
        if loc is None:
            continue
        del loc[0][loc[1]]
        yield Mutant("delete-producer", "FC001", var, None,
                     f"delete '{w.name}', the only writer of '{var}'", m)


def _rename_read(an: Analysis, data: dict) -> Iterator[Mutant]:
    seen = set()
    for n in an.wf.nodes.values():
        if n.type not in ("step", "task"):
            continue
        for r in n.reads:
            if r.kind != "template" or r.field != "instruction" or r.path != r.name:
                continue
            if (n.id, r.name) in seen or not an.surely_defined(n, r.name):
                continue
            seen.add((n.id, r.name))
            m = copy.deepcopy(data)
            body = _body(m, n)
            if body is None or not isinstance(body.get("instruction"), str):
                continue
            new = r.name + RENAMED_SUFFIX
            body["instruction"] = body["instruction"].replace(
                "{{" + r.name + "}}", "{{" + new + "}}", 1)
            yield Mutant("rename-read", "FC001", new, n.name,
                         f"rename {{{{{r.name}}}}} to {{{{{new}}}}} in '{n.name}'", m)


def _remove_branch_write(an: Analysis, data: dict) -> Iterator[Mutant]:
    wf = an.wf
    for h in wf.nodes.values():
        if h.type not in ("if", "switch"):
            continue
        arms = h.extra.get("arm_scopes", {})
        if h.type == "if" and len(arms) != 2:
            continue
        for var in _defined_vars(an):
            if an.surely_defined(h, var):
                continue
            per_arm: Dict[str, List[Node]] = {}
            for lbl, sid in arms.items():
                per_arm[lbl] = [w for w in an.writers.get(var, [])
                                if sid in wf.scope_chain(w.scope)]
            if not all(per_arm.values()):
                continue
            join = next(n for n in wf.nodes.values()
                        if n.type == "join" and n.extra.get("header") == h.id)
            readers = [r for r in _template_readers(an, var) if an.reaches(join.id, r.id)
                       and not any(sid in wf.scope_chain(r.scope) for sid in arms.values())]
            if not readers:
                continue
            for lbl, ws in per_arm.items():
                m = copy.deepcopy(data)
                ok = True
                for w in sorted(ws, key=lambda x: -x.id):  # delete later ones first
                    if w.type in ("step", "task"):
                        body = _body(m, w)
                        if body is None:
                            ok = False
                            break
                        body.pop("save_as", None)
                    else:
                        loc = _locate(m, w)
                        if loc is None:
                            ok = False
                            break
                        del loc[0][loc[1]]
                if ok:
                    yield Mutant("remove-branch-write", "FC002", var, None,
                                 f"remove the write of '{var}' from the '{lbl}' arm of "
                                 f"'{h.name}'", m)


def _move_reader(an: Analysis, data: dict) -> Iterator[Mutant]:
    for var in _defined_vars(an):
        w = _single_writer(an, var)
        if w is None or _top_index(w) is None:
            continue
        for r in _template_readers(an, var):
            j, i = _top_index(r), _top_index(w)
            if j is None or j <= i:
                continue
            m = copy.deepcopy(data)
            step = m["workflow"].pop(j)
            m["workflow"].insert(i, step)
            yield Mutant("move-reader-before-writer", "FC001", var, r.name,
                         f"move '{r.name}' before '{w.name}', the only writer of '{var}'", m)
            break


def _parallel_no_export(an: Analysis, data: dict) -> Iterator[Mutant]:
    for var in _defined_vars(an):
        w = _single_writer(an, var)
        if w is None or w.type not in ("step", "task"):
            continue
        if not any(an.reaches(w.id, r.id) for r in _template_readers(an, var)):
            continue
        m = copy.deepcopy(data)
        loc = _locate(m, w)
        if loc is None:
            continue
        lst, idx = loc
        lst[idx] = {"parallel": [lst[idx]]}
        yield Mutant("parallel-no-export", "FC003", var, None,
                     f"wrap '{w.name}', the only writer of '{var}', in a parallel block", m)


def _step_to_task(an: Analysis, data: dict) -> Iterator[Mutant]:
    for n in an.wf.nodes.values():
        if n.type != "step" or _isolated(an, n):
            continue
        reads = {r.name for r in n.reads}
        if "prev_output" in reads or reads & _upstream_producers(an, n):
            continue
        m = copy.deepcopy(data)
        loc = _locate(m, n)
        if loc is None:
            continue
        lst, idx = loc
        lst[idx] = {"task": lst[idx]["step"]}
        yield Mutant("step-to-task", "FC004", None, n.name,
                     f"turn step '{n.name}' (history is its only source of earlier work) "
                     "into a task", m)


GENERATORS = {
    "delete-producer": _delete_producer,
    "rename-read": _rename_read,
    "remove-branch-write": _remove_branch_write,
    "move-reader-before-writer": _move_reader,
    "parallel-no-export": _parallel_no_export,
    "step-to-task": _step_to_task,
}


def generate(wf: Workflow) -> List[Mutant]:
    """All mutants of a parsed workflow (``wf.raw`` is the YAML data)."""
    an = Analysis(wf)
    out: List[Mutant] = []
    for gen in GENERATORS.values():
        out.extend(gen(an, wf.raw))
    return out


def detected(mutant: Mutant, original_keys: set, findings) -> bool:
    """Did a *new* finding of the expected rule (and variable or node) appear?"""
    for f in findings:
        if f.key in original_keys or f.rule_id != mutant.expect_rule:
            continue
        if mutant.expect_var is not None and f.variable != mutant.expect_var:
            continue
        if mutant.expect_var is None and f.node_name != mutant.expect_node:
            continue
        return True
    return False
