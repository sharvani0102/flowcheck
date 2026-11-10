"""Template placeholders and condition expressions, as AgentSPEX parses them."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .ir import Read

# Adapted from AgentSPEX (Lean4Agent, MIT, (c) 2026 Ruida Wang; see
# THIRD_PARTY_NOTICES.md): AgentSPEX/src/harness/parsing/templates.py:23.
# The captured text is NOT stripped by the runtime, so "{{ x }}" never resolves.
TEMPLATE_RE = re.compile(r"\{\{([^}]+)\}\}")

# Adapted from AgentSPEX (Lean4Agent, MIT, (c) 2026 Ruida Wang; see
# THIRD_PARTY_NOTICES.md): AgentSPEX/src/harness/parsing/conditions.py:142-146
# and :211-212, so FlowCheck accepts exactly the runtime's condition grammar.
_IDENT = r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*"
_NUMBER = r"[+-]?[0-9]+(?:\.[0-9]+)?"
_QUOTED = r'"[^"]*"|\'[^\']*\''
COMPARISON_RE = re.compile(
    rf"^({_IDENT}|{_NUMBER}|{_QUOTED})\s*(==|!=|<=|>=|<|>)\s*({_IDENT}|{_NUMBER}|{_QUOTED})$"
)
AND_SPLIT = re.compile(r"\s+and\s+")
OR_SPLIT = re.compile(r"\s+or\s+")
# (end of adapted code)

IDENT_RE = re.compile(rf"^{_IDENT}$")
# A placeholder that looks like a variable reference, possibly with spaces.
# Anything else ("{{"key": 1}}", "{{x | y}}") is not a variable reference and
# is left alone, as the author presumably intended literal text.
_LOOSE_NAME_RE = re.compile(r"^\s*[A-Za-z_]\w*(?:\.\w+)*\s*$")
_TPL_PLACEHOLDER = "__flowcheck_tpl__"


def has_template(text: object) -> bool:
    return isinstance(text, str) and bool(TEMPLATE_RE.search(text))


def template_reads(text: object, field: str, line: Optional[int] = None) -> List[Read]:
    """Reads for every variable-like ``{{...}}`` in ``text``.

    ``name`` is what the runtime looks up: the root before the first dot,
    with any whitespace kept (``templates.py:12-20``).
    """
    if text is None:
        return []
    if not isinstance(text, str):
        text = str(text)
    reads = []
    for m in TEMPLATE_RE.finditer(text):
        inner = m.group(1)
        if not _LOOSE_NAME_RE.match(inner):
            continue
        root = inner.split(".", 1)[0] if "." in inner else inner
        reads.append(Read(name=root, path=inner, field=field, kind="template", line=line))
    return reads


def template_to_pattern(text: str) -> str:
    """Regex matching the names a templated ``save_as`` can produce."""
    parts = TEMPLATE_RE.split(text)
    # split() returns literal, group, literal, group, ...
    out = []
    for i, part in enumerate(parts):
        out.append(re.escape(part) if i % 2 == 0 else ".+")
    return "^" + "".join(out) + "$"


def condition_reads(condition: object, field: str, line: Optional[int] = None
                    ) -> Tuple[List[Read], Optional[str]]:
    """Reads in an ``if``/``while`` condition, and a problem description if
    some atom can never be true under AgentSPEX's grammar."""
    text = "" if condition is None else str(condition)
    reads = template_reads(text, field, line)
    # Templates are expanded before evaluation (control.py:251, 337); treat
    # each one as an opaque identifier for the grammar check.
    stripped = TEMPLATE_RE.sub(_TPL_PLACEHOLDER, text).strip()
    if not stripped:
        return reads, "the condition is empty, so it is always false (conditions.py:238-239)"
    problem = None
    for or_clause in OR_SPLIT.split(stripped):
        for atom in AND_SPLIT.split(or_clause):
            atom = atom.strip()
            m = COMPARISON_RE.match(atom)
            if m:
                for operand in (m.group(1), m.group(3)):
                    if IDENT_RE.match(operand) and _TPL_PLACEHOLDER not in operand:
                        root = operand.split(".", 1)[0]
                        reads.append(Read(name=root, path=operand, field=field,
                                          kind="cond", line=line))
                continue
            if IDENT_RE.match(atom):
                if _TPL_PLACEHOLDER not in atom:
                    root = atom.split(".", 1)[0]
                    reads.append(Read(name=root, path=atom, field=field,
                                      kind="truth", line=line))
                continue
            if problem is None:
                shown = atom.replace(_TPL_PLACEHOLDER, "{{...}}")
                problem = (
                    f"the condition part '{shown}' is neither a comparison "
                    "(== != < > <= >=) nor a variable name, so AgentSPEX "
                    "evaluates it as false every time (conditions.py:142-206)"
                )
    return reads, problem
