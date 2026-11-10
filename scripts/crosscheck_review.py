"""Refresh tests/crosscheck/expected_mismatches.yaml from the current code.

Existing reviewed entries keep their category and note. New mismatches are
added with category UNREVIEWED (the test fails until a human reviews them),
and entries that no longer occur are dropped and listed on stdout.

    python scripts/crosscheck_review.py
"""

from pathlib import Path

import yaml

from flowcheck.crosscheck import compare_all

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "tests" / "crosscheck" / "expected_mismatches.yaml"


def main() -> None:
    doc = yaml.safe_load(FILE.read_text()) if FILE.exists() else {}
    doc = doc or {}
    old = {(e["workflow"], e["kind"], e["node"], e["detail"]): e
           for e in doc.get("mismatches", [])}
    new = []
    for m in compare_all(ROOT / "reference" / "Lean4Agent"):
        e = dict(m.to_dict())
        prev = old.pop(m.key, None)
        e["category"] = prev["category"] if prev else "UNREVIEWED"
        e["note"] = prev.get("note", "") if prev else ""
        new.append(e)
    for k in old:
        print("stale (removed):", k)
    doc["mismatches"] = new
    FILE.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100))
    print(f"{len(new)} mismatches, "
          f"{sum(e['category'] == 'UNREVIEWED' for e in new)} unreviewed -> {FILE}")


if __name__ == "__main__":
    main()
