"""Each fixture states its exact expected findings in '# expect:' lines.

    # expect: FC001 node=<step name> var=<variable>

A file with no expect lines must produce no findings at all.
"""

import shlex
from pathlib import Path

import pytest

from flowcheck import check_file

from conftest import FIXTURES

FILES = sorted(FIXTURES.rglob("*.yaml"))


def expected(path: Path):
    out = set()
    for line in path.read_text().splitlines():
        if not line.startswith("# expect:"):
            continue
        parts = shlex.split(line[len("# expect:"):])
        rule, kv = parts[0], dict(p.split("=", 1) for p in parts[1:])
        out.add((rule, kv.get("node"), kv.get("var")))
    return out


def actual(path: Path):
    res = check_file(str(path))
    assert res.parse_error is None, res.parse_error
    return {(f.rule_id, f.node_name, f.variable) for f in res.findings}, res


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(FIXTURES)))
def test_fixture(path):
    exp = expected(path)
    if path.name.startswith("buggy_"):
        assert exp, "buggy fixtures must declare what they expect"
    got, res = actual(path)
    detail = "\n".join(f"  {f.rule_id} [{f.node_name}] {f.variable}: {f.message}"
                       for f in res.findings)
    assert got == exp, f"findings differ:\n{detail}"


def test_every_rule_has_clean_and_buggy_fixtures():
    for rule in ("fc001", "fc002", "fc003", "fc004", "fc005"):
        names = [p.name for p in (FIXTURES / rule).glob("*.yaml")]
        assert any(n.startswith("buggy_") for n in names), rule
        if rule != "fc005":
            assert any(n.startswith("clean_") for n in names), rule
