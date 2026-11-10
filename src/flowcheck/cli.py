"""Command line: ``flowcheck FILE_OR_DIR... [--format text|json]``.

Exit codes: 0 no errors, 1 errors found, 2 at least one file failed to parse.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__, check_file
from .report import format_json, format_text


def _expand(paths: List[str]) -> List[str]:
    out = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            out += sorted(str(f) for f in pp.rglob("*.y*ml") if f.suffix in (".yaml", ".yml"))
        else:
            out.append(p)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="flowcheck",
        description="Static checker for AgentSPEX workflows: finds steps that assume "
                    "information they were never given.")
    ap.add_argument("paths", nargs="+", metavar="PATH",
                    help="workflow YAML files, or directories to search for *.yaml")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--rules", help="comma-separated rule ids to run (default: all)")
    ap.add_argument("--version", action="version", version=f"flowcheck {__version__}")
    args = ap.parse_args(argv)

    rules = [r.strip().upper() for r in args.rules.split(",")] if args.rules else None
    files = _expand(args.paths)
    if not files:
        ap.error("no YAML files found")
    results = [check_file(f, rules) for f in files]
    out = format_json(results) if args.format == "json" else format_text(results)
    sys.stdout.write(out)
    if any(r.parse_error for r in results):
        return 2
    if any(r.errors for r in results):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
