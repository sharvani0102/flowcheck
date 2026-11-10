from flowcheck.checks import run_checks
from flowcheck.mutate import detected, generate
from flowcheck.parser import parse_file, parse_text

from conftest import FIXTURES


def kinds(path):
    wf = parse_file(path)
    original = {f.key for f in run_checks(wf)}
    out = {}
    for m in generate(wf):
        ok = detected(m, original, run_checks(parse_text(m.text(), str(path))))
        out.setdefault(m.kind, []).append(ok)
    return out


def test_branch_write_mutant_is_detected():
    res = kinds(FIXTURES / "fc002" / "clean_both_branches.yaml")
    assert res["remove-branch-write"] and all(res["remove-branch-write"])


def test_structural_mutants_on_clean_fixture():
    res = kinds(FIXTURES / "fc001" / "clean_params_and_builtins.yaml")
    for k in ("delete-producer", "rename-read", "parallel-no-export"):
        assert res.get(k) and all(res[k]), k


def test_mutant_yaml_parses():
    wf = parse_file(FIXTURES / "fc002" / "clean_loops.yaml")
    for m in generate(wf):
        parse_text(m.text())
