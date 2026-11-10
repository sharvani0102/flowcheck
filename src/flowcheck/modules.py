"""Best-effort lookup of sub-module files, used only to explain FC003.

AgentSPEX resolves ``modules/...`` and ``workflows/...`` against the root
workflow's directory and other relative paths against the calling file's
directory, falling back to the current directory (``calls.py:114-137``).
When a module file is analysed on its own we don't know the root, so we also
try the calling file's ancestor directories.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, Optional

from .templates import has_template


def resolve_module(module: str, workflow_path: str) -> Optional[Path]:
    if not module or has_template(module):
        return None
    p = Path(module)
    if p.is_absolute():
        return p if p.is_file() else None
    here = Path(workflow_path).resolve().parent
    candidates = [here / p] + [anc / p for anc in list(here.parents)[:4]] + [Path.cwd() / p]
    for c in candidates:
        if c.is_file():
            return c
    return None


@lru_cache(maxsize=128)
def module_internal_writes(path: str) -> FrozenSet[str]:
    """Every variable name a module file writes in its own context."""
    from .parser import ParseError, parse_file  # local import: avoid a cycle

    try:
        wf = parse_file(path)
    except ParseError:
        return frozenset()
    return frozenset(w.name for n in wf.nodes.values() for w in n.writes if w.pattern is None)
