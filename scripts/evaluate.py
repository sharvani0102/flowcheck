"""Phase-one evaluation.

    python scripts/evaluate.py

Writes:
  results/phase1_eval.json   source of truth (one record per workflow + summaries)
  docs/phase1-results.md     tables rendered from that JSON (never hand-edit)
  results/demo.txt           CLI output on one failed and one passed workflow

Hand-written review input (validated here, the run fails if it is
incomplete or contradicts the findings):
  results/annotations/lean4agent_problems.yaml
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "reference" / "Lean4Agent"
sys.path.insert(0, str(ROOT / "src"))

from flowcheck import __version__, check_file  # noqa: E402
from flowcheck.cli import main as cli_main  # noqa: E402
from flowcheck.crosscheck import benchmark_pairs, compare_all  # noqa: E402

SPEC_RULES = {"FC001", "FC002", "FC003", "FC004"}
OUT_JSON = ROOT / "results" / "phase1_eval.json"
OUT_MD = ROOT / "docs" / "phase1-results.md"
OUT_DEMO = ROOT / "results" / "demo.txt"
ANNOTATIONS = ROOT / "results" / "annotations" / "lean4agent_problems.yaml"
CROSSCHECK = ROOT / "tests" / "crosscheck" / "expected_mismatches.yaml"
DEMO_FILES = ["experiments/SWE_bench_verified/failed_workflow_3.yaml",
              "experiments/SWE_bench_verified/passed_workflow_1.yaml"]


class EvalError(Exception):
    pass


def rel(p: Path) -> str:
    return str(Path(p).resolve().relative_to(REF.resolve()))


def verdict(findings: List[Dict[str, Any]]) -> str:
    if any(f["severity"] == "error" for f in findings):
        return "fail"
    if findings:
        return "warn"
    return "pass"


def reference_commit() -> str:
    out = subprocess.run(["git", "-C", str(REF), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


# -- 4a: benchmark workflows -------------------------------------------------

def run_benchmarks() -> List[Dict[str, Any]]:
    records = []
    for label, yaml_path, _ in benchmark_pairs(REF):
        bench, stem = label.split("/")
        res = check_file(str(yaml_path))
        if res.parse_error:
            raise EvalError(f"{label}: parse error: {res.parse_error}")
        findings = [f.to_dict() for f in res.findings]
        spec = [f for f in findings if f["rule_id"] in SPEC_RULES]
        records.append({
            "id": label,
            "benchmark": {"swe": "SWE-Bench Verified", "elaip": "ELAIP-Bench"}[bench],
            "file": rel(yaml_path),
            "lean4agent_label": "passed" if stem.startswith("passed") else "failed",
            "verdict": verdict(findings),
            "verdict_spec_rules": verdict(spec),
            "counts": dict(Counter(f["rule_id"] for f in findings)),
            "findings": findings,
        })
    return records


def agreement(records: List[Dict[str, Any]], key: str, flag: str) -> Dict[str, int]:
    """Confusion counts with 'failed' as the positive class.

    flag = "errors": flagged means verdict fail; flag = "any": fail or warn.
    """
    flagged_v = {"errors": {"fail"}, "any": {"fail", "warn"}}[flag]
    c = Counter()
    for r in records:
        flagged = r[key] in flagged_v
        pos = r["lean4agent_label"] == "failed"
        c[("TP" if flagged else "FN") if pos else ("FP" if flagged else "TN")] += 1
    return {k: c.get(k, 0) for k in ("TP", "FN", "FP", "TN")}


def _match(f: Dict[str, Any], rule: str, node: str, var: Any = None) -> bool:
    return f["rule_id"] == rule and f["node_name"] == node and (var is None or f["variable"] == var)


def review_problems(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    ann = yaml.safe_load(ANNOTATIONS.read_text())
    by_id = {r["id"]: r for r in records}
    failed_ids = [r["id"] for r in records if r["lean4agent_label"] == "failed"]
    missing = set(failed_ids) - set(ann["problems"])
    if missing:
        raise EvalError(f"no documented-problem review for: {sorted(missing)}")
    out = {}
    for wid, entry in ann["problems"].items():
        findings = by_id[wid]["findings"]
        items = []
        for it in entry["items"]:
            cls = it["classification"]
            if cls not in ("caught", "needs-annotations", "flowcheck-gap"):
                raise EvalError(f"{wid}: bad classification {cls!r}")
            if cls == "caught":
                exp = it.get("expect") or []
                if not exp:
                    raise EvalError(f"{wid}: 'caught' without expected findings: {it['text']}")
                for e in exp:
                    if not any(_match(f, e["rule"], e["node"], e.get("var")) for f in findings):
                        raise EvalError(f"{wid}: claimed caught but no finding {e}")
            items.append(dict(it))
        for x in entry.get("extra_findings") or []:
            if not any(_match(f, x["rule"], x["node"]) for f in findings):
                raise EvalError(f"{wid}: extra finding not present: {x}")
        out[wid] = {k: v for k, v in entry.items() if k != "items"} | {"items": items}
    # every finding on a passed workflow must be reviewed
    reviews = ann.get("passed_findings") or []
    for r in records:
        if r["lean4agent_label"] != "passed":
            continue
        for f in r["findings"]:
            if not any(v["workflow"] == r["id"] and _match(f, v["rule"], v["node"], v.get("var"))
                       for v in reviews):
                raise EvalError(f"unreviewed finding on passed workflow {r['id']}: "
                                f"{f['rule_id']} {f['node_name']} {f['variable']}")
    for v in reviews:
        rec = by_id[v["workflow"]]
        if not any(_match(f, v["rule"], v["node"], v.get("var")) for f in rec["findings"]):
            raise EvalError(f"stale passed-finding review: {v}")
    totals = Counter(it["classification"] for e in out.values() for it in e["items"])
    return {"workflows": out, "totals": dict(totals), "passed_findings": reviews}


def crosscheck_summary() -> Dict[str, Any]:
    doc = yaml.safe_load(CROSSCHECK.read_text())
    computed = {m.key for m in compare_all(REF)}
    listed = {(e["workflow"], e["kind"], e["node"], e["detail"]) for e in doc["mismatches"]}
    if computed != listed:
        raise EvalError("cross-check mismatches differ from the reviewed list; "
                        "run pytest tests/test_crosscheck.py")
    per = Counter((e["workflow"], e["category"]) for e in doc["mismatches"])
    return {
        "total": len(doc["mismatches"]),
        "by_category": dict(Counter(e["category"] for e in doc["mismatches"])),
        "by_workflow": {w: {c: n for (w2, c), n in per.items() if w2 == w}
                        for w in sorted({e["workflow"] for e in doc["mismatches"]})},
        "categories": doc["categories"],
    }


# -- demo -----------------------------------------------------------------

def make_demo() -> str:
    parts = []
    import os
    cwd = os.getcwd()
    os.chdir(REF)
    try:
        for f in DEMO_FILES:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli_main([f])
            parts.append(f"$ flowcheck {f}\n{buf.getvalue()}[exit code {code}]\n")
    finally:
        os.chdir(cwd)
    header = ("FlowCheck demo (run from reference/Lean4Agent, commit "
              f"{reference_commit()}; flowcheck {__version__})\n"
              "One workflow Lean4Agent rejects and one it accepts.\n\n")
    return header + "\n".join(parts)


# -- markdown -------------------------------------------------------------

def md_escape(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def short_finding(f: Dict[str, Any]) -> str:
    var = f" `{f['variable']}`" if f.get("variable") else ""
    sev = "E" if f["severity"] == "error" else "W"
    return f"{f['rule_id']}({sev}){var} @ {md_escape(f['node_name'])}"


def render(data: Dict[str, Any]) -> str:
    L: List[str] = []
    a = L.append
    a("# FlowCheck phase one: results")
    a("")
    a("> Generated by `scripts/evaluate.py` from `results/phase1_eval.json`. Do not edit by hand.")
    a("")
    a(f"FlowCheck {data['flowcheck_version']}, Lean4Agent reference commit "
      f"`{data['reference_commit']}`. Rules: FC001-FC004 are the original spec, FC005 "
      "(runtime traps) is an addition and is reported separately where it matters. "
      "**E** = error (certain from the YAML), **W** = warning (heuristic or path-insensitive).")
    a("")
    if not data.get("fc004_implemented"):
        a("**Status:** FC004 (isolated context) is not implemented yet in this run, so problems "
          "that only FC004 could catch are listed as gaps for now.")
        a("")

    # 4a
    a("## 4a. The 12 labelled benchmark workflows")
    a("")
    a("Verdict: `fail` = at least one error, `warn` = warnings only, `pass` = no findings. "
      "\"Spec rules\" ignores FC005.")
    a("")
    a("| Workflow | Lean4Agent label | Verdict (all rules) | Verdict (spec rules FC001-FC004) | Findings |")
    a("|---|---|---|---|---|")
    for r in data["benchmarks"]:
        fs = "<br>".join(short_finding(f) for f in r["findings"]) or "none"
        a(f"| `{r['id']}` | {r['lean4agent_label']} | {r['verdict']} | {r['verdict_spec_rules']} | {fs} |")
    a("")
    a("### Agreement with Lean4Agent's labels")
    a("")
    a("Positive class = Lean4Agent rejects the workflow (`failed`). TP = failed and flagged, "
      "FN = failed and not flagged, FP = passed and flagged, TN = passed and not flagged.")
    a("")
    a("| Rules | Flagged means | TP | FN | FP | TN |")
    a("|---|---|---|---|---|---|")
    for row in data["agreement"]:
        c = row["counts"]
        a(f"| {row['rules']} | {row['flag']} | {c['TP']} | {c['FN']} | {c['FP']} | {c['TN']} |")
    a("")

    # documented problems
    pr = data["lean4agent_problems"]
    a("### Problems Lean4Agent documents for its failed workflows")
    a("")
    t = pr["totals"]
    a(f"Totals: **caught {t.get('caught', 0)}**, needs per-step annotations (phase two) "
      f"{t.get('needs-annotations', 0)}, FlowCheck gap {t.get('flowcheck-gap', 0)}.")
    a("")
    for wid, e in pr["workflows"].items():
        a(f"#### `{wid}`")
        a("")
        a(f"Source: `{e['source']}`. Lean4Agent verdict: {e['lean_verdict']}.")
        if e.get("stale"):
            a("")
            a(f"**Note:** {md_escape(e['stale'])}")
        a("")
        a("| Documented problem | Classification | Reasoning |")
        a("|---|---|---|")
        for it in e["items"]:
            a(f"| {md_escape(it['text'])} | {it['classification']} | {md_escape(it['reasoning'])} |")
        if e.get("extra_findings"):
            a("")
            a("Problems FlowCheck reports that Lean4Agent doesn't list:")
            a("")
            for x in e["extra_findings"]:
                a(f"- **{x['rule']}** at `{md_escape(x['node'])}`: {md_escape(x['note'])}")
        a("")

    a("### Findings on passed workflows")
    a("")
    pf = pr["passed_findings"]
    if not pf:
        a("None.")
    else:
        a("| Workflow | Finding | Review | Reasoning |")
        a("|---|---|---|---|")
        for v in pf:
            var = f" `{v['var']}`" if v.get("var") else ""
            a(f"| `{v['workflow']}` | {v['rule']}{var} @ {md_escape(v['node'])} | "
              f"{v['verdict']} | {md_escape(v['reasoning'])} |")
    a("")

    a("### All findings in full")
    a("")
    for r in data["benchmarks"]:
        if not r["findings"]:
            continue
        a(f"**`{r['file']}`**")
        a("")
        for f in r["findings"]:
            a(f"- line {f['line']}: {f['severity']} **{f['rule_id']}** [{md_escape(f['node_name'])}] "
              f"{md_escape(f['message'])}  ")
            if f.get("hint"):
                a(f"  hint: {md_escape(f['hint'])}")
        a("")

    # cross-check
    cc = data["crosscheck"]
    a("## Graph cross-check against Lean4Agent's IR")
    a("")
    a(f"FlowCheck's parsed nodes, edges, reads and writes were compared with the 12 IR files "
      f"in `FormalAgentLib/VerificationExamples/`. **{cc['total']} differences**, every one "
      "reviewed in `tests/crosscheck/expected_mismatches.yaml`; `pytest` fails if that list "
      "and the computed differences ever disagree. None is a FlowCheck bug.")
    a("")
    a("| Category | Count | Explanation |")
    a("|---|---|---|")
    for cat, n in sorted(cc["by_category"].items(), key=lambda kv: -kv[1]):
        a(f"| {cat} | {n} | {md_escape(cc['categories'][cat])} |")
    a("")
    a("| Workflow | Differences by category |")
    a("|---|---|")
    for w, cats in cc["by_workflow"].items():
        a(f"| `{w}` | " + ", ".join(f"{c} {n}" for c, n in sorted(cats.items())) + " |")
    a("")
    return "\n".join(L) + "\n"


def main() -> int:
    try:
        records = run_benchmarks()
        data = {
            "flowcheck_version": __version__,
            "reference_commit": reference_commit(),
            "fc004_implemented": False,
            "benchmarks": records,
            "agreement": [
                {"rules": rules, "flag": flag,
                 "counts": agreement(records, key, flag)}
                for rules, key in (("all (FC001-FC005)", "verdict"),
                                   ("spec (FC001-FC004)", "verdict_spec_rules"))
                for flag in ("errors", "any")
            ],
            "lean4agent_problems": review_problems(records),
            "crosscheck": crosscheck_summary(),
        }
    except EvalError as e:
        print(f"evaluation failed: {e}", file=sys.stderr)
        return 1
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, indent=2) + "\n")
    OUT_MD.write_text(render(data))
    OUT_DEMO.write_text(make_demo())
    print(f"wrote {OUT_JSON.relative_to(ROOT)}, {OUT_MD.relative_to(ROOT)}, "
          f"{OUT_DEMO.relative_to(ROOT)}")
    for row in data["agreement"]:
        print(row["rules"], row["flag"], row["counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
