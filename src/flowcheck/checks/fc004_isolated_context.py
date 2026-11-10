"""FC004 isolated-context (warning, heuristic).

A node that cannot see earlier work, but whose instruction refers to it.

Which nodes are isolated (semantics §4):
- a ``task``: it gets only the system prompt and its own instruction
  (AgentSPEX llm_executor.py:263-268);
- a ``step`` with no earlier ``step`` in its history scope: tasks never
  append to the conversation history, so a step that follows only tasks sees
  no earlier turns.

An isolated node is reported only if it also receives **no upstream-produced
variable**: none of its ``{{...}}`` reads (including ``prev_output``) comes
from an earlier step's output. If it receives some earlier output we can't
tell which work a phrase refers to, so we stay quiet (precision over recall).

It is then flagged when its instruction contains one of the fixed phrases in
``PHRASES``, or names an upstream variable in plain text (a snake_case name
another step writes, e.g. "Source: question_analysis") without templating
it.

This is a heuristic. Tools are a side channel: a task can read files that
earlier steps wrote (fs_read, shell), which FlowCheck can't see. The phrase
list was fixed before FC004 was run on any benchmark workflow.
"""

from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple

from ..findings import WARNING, Finding
from ..ir import Node
from ..templates import TEMPLATE_RE
from .common import Analysis, finding

RULE = "FC004"

#: Phrases that refer to earlier work. Fixed before evaluation; do not tune to labels.
PHRASES = (
    "your fix", "your patch", "your changes", "the changes you made", "the fix you",
    "you made", "you implemented", "you identified", "you found", "you created",
    "you wrote", "you just", "the previous", "previous step", "previous steps",
    "earlier step", "from earlier", "as before", "above",
)
#: "above" can refer to text placed earlier in the same prompt, so it only
#: counts if no {{...}} placeholder appears before it.
POSITIONAL = {"above"}

_PHRASE_RES = [(p, re.compile(r"(?<![\w-])" + re.escape(p) + r"(?![\w-])", re.IGNORECASE))
               for p in PHRASES]
_SNAKE_RE = re.compile(r"(?<![\w{])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?![\w}])")


def _isolated(an: Analysis, node: Node) -> bool:
    if node.type == "task":
        return True
    if node.type != "step":
        return False
    chain = set(an.wf.scope_chain(node.scope))
    for other in an.wf.nodes.values():
        if other.type != "step" or other.id == node.id or not an.reaches(other.id, node.id):
            continue
        # A step inside a discarded parallel child appends to a throwaway copy.
        lost = any(an.wf.scopes[s].discards_writes and s not in chain
                   for s in an.wf.scope_chain(other.scope))
        if not lost:
            return False
    return True


def _upstream_producers(an: Analysis, node: Node) -> Set[str]:
    """Names written by real steps (not loop bindings) that can reach ``node``."""
    out = set()
    for name, writers in an.writers.items():
        for w in writers:
            if w.type != "bind" and w.id != node.id and an.reaches(w.id, node.id):
                out.add(name)
    return out


def _phrase_hit(instruction: str) -> Optional[str]:
    """The earliest qualifying phrase in the instruction, as written."""
    first_tpl = TEMPLATE_RE.search(instruction)
    hits = []
    for phrase, rx in _PHRASE_RES:
        for m in rx.finditer(instruction):
            if phrase in POSITIONAL and first_tpl and first_tpl.start() < m.start():
                continue
            hits.append((m.start(), m.group(0)))
    return min(hits)[1] if hits else None


def _bare_mention(instruction: str, upstream: Set[str], read_names: Set[str]) -> Optional[str]:
    text = TEMPLATE_RE.sub(" ", instruction)
    for m in _SNAKE_RE.finditer(text):
        name = m.group(1)
        if name in upstream and name not in read_names:
            return name
    return None


def check(an: Analysis) -> List[Finding]:
    out = []
    for node in an.wf.nodes.values():
        if node.type not in ("step", "task") or not node.instruction:
            continue
        if not _isolated(an, node):
            continue
        upstream = _upstream_producers(an, node)
        read_names = {r.name for r in node.reads}
        if "prev_output" in read_names or read_names & upstream:
            continue  # it is given some earlier output
        what: Tuple[str, str]
        mention = _bare_mention(node.instruction, upstream, read_names)
        phrase = _phrase_hit(node.instruction)
        if mention:
            what = (f"names '{mention}' in plain text", "{{" + mention + "}}")
        elif phrase:
            cands = sorted(upstream)[:3]
            what = (f"says \"{phrase}\"",
                    " or ".join("{{" + c + "}}" for c in cands) if cands else "")
        else:
            continue
        kind = ("a task, which has no conversation history"
                if node.type == "task" else
                "a step with no earlier step in its history (tasks don't add to it)")
        hint = (f"pass the earlier result explicitly, e.g. {what[1]}" if what[1] else
                "save the earlier result with save_as and pass it with {{...}}")
        out.append(finding(
            RULE, WARNING, node,
            f"its instruction {what[0]}, referring to earlier work, but this is {kind} and "
            "it receives no earlier step's output through {{...}}. Unless it reads that work "
            "back from files with a tool, it cannot see it",
            hint, variable=mention))
    return out
