"""Text and JSON output."""

from __future__ import annotations

import json
from typing import List

from . import FileResult, __version__


def format_text(results: List[FileResult]) -> str:
    lines = []
    n_err = n_warn = n_parse = 0
    for r in results:
        if r.parse_error:
            n_parse += 1
            lines.append(f"{r.path}: parse error: {r.parse_error}")
            continue
        for f in r.findings:
            where = f"{r.path}:{f.line}" if f.line else r.path
            var = f" var '{f.variable}':" if f.variable else ""
            lines.append(f"{where}: {f.severity} {f.rule_id} [{f.node_name} ({f.node_type})]"
                         f"{var} {f.message}")
            if f.hint:
                lines.append(f"    hint: {f.hint}")
        n_err += len(r.errors)
        n_warn += len(r.warnings)
    summary = f"{n_err} error(s), {n_warn} warning(s) in {len(results)} file(s)"
    if n_parse:
        summary += f", {n_parse} parse failure(s)"
    lines.append(summary)
    return "\n".join(lines) + "\n"


def to_json_obj(results: List[FileResult]) -> dict:
    return {
        "flowcheck_version": __version__,
        "files": [
            {
                "path": r.path,
                "workflow": r.workflow,
                "verdict": r.verdict,
                "parse_error": r.parse_error,
                "findings": [f.to_dict() for f in r.findings],
            }
            for r in results
        ],
        "summary": {
            "files": len(results),
            "errors": sum(len(r.errors) for r in results),
            "warnings": sum(len(r.warnings) for r in results),
            "parse_failures": sum(1 for r in results if r.parse_error),
        },
    }


def format_json(results: List[FileResult]) -> str:
    return json.dumps(to_json_obj(results), indent=2) + "\n"
