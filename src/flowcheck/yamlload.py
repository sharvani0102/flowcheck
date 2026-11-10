"""Load AgentSPEX YAML the way the runtime does, keeping line numbers.

AgentSPEX substitutes ``${VAR}`` / ``${VAR:-default}`` in the raw text and
then calls ``yaml.safe_load`` (AgentSPEX ``parsing/yaml_parser.py:22-74``).
FlowCheck reproduces the substitution *without reading the real
environment*: ``${VAR:-default}`` becomes ``default`` and a bare ``${VAR}``
stays literal, exactly what AgentSPEX does when the variable is unset. The
analysis does not depend on parameter values, only on whether they exist.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

import yaml

_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def expand_env_defaults(text: str) -> str:
    """Apply AgentSPEX's ``${VAR:-default}`` rule as if no variable were set."""

    def repl(match: "re.Match[str]") -> str:
        expr = match.group(1)
        if ":-" not in expr:
            return match.group(0)
        value = expr.split(":-", 1)[1]
        # AgentSPEX escapes values that came from the environment
        # (yaml_parser.py:50-67); a default counts as such a value.
        if any(c in value for c in ('\\', '"', "\n", "\r", "\t")):
            value = (
                value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t")
            )
        return value

    return _ENV_RE.sub(repl, text)


class _LineLoader(yaml.SafeLoader):
    """SafeLoader that remembers the 1-based start line of every mapping."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self.lines: Dict[int, int] = {}

    def construct_yaml_map(self, node):  # type: ignore[override]
        data: Dict[Any, Any] = {}
        self.lines[id(data)] = node.start_mark.line + 1
        yield data
        data.update(self.construct_mapping(node))


_LineLoader.add_constructor("tag:yaml.org,2002:map", _LineLoader.construct_yaml_map)


def load_yaml(text: str) -> Tuple[Any, Dict[int, int]]:
    """Return ``(data, lines)`` where ``lines`` maps ``id(mapping) -> line``.

    Raises ``yaml.YAMLError`` on malformed YAML. The returned objects must be
    kept alive while ``lines`` is used, because it is keyed by ``id()``.
    """
    loader = _LineLoader(expand_env_defaults(text))
    try:
        data = loader.get_single_data()
    finally:
        loader.dispose()
    return data, loader.lines
