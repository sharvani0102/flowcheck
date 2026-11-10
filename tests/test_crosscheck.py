"""Graph cross-check against Lean4Agent's 12 benchmark IR files.

Every difference between FlowCheck's IR and Lean4Agent's is listed, reviewed
and categorised in tests/crosscheck/expected_mismatches.yaml. This test fails
if a new difference appears, if a documented one disappears, or if any entry
is unreviewed. It never ignores a mismatch.
"""

from collections import Counter

import pytest
import yaml

from flowcheck.crosscheck import benchmark_pairs, compare_all

from conftest import ROOT

EXPECTED = ROOT / "tests" / "crosscheck" / "expected_mismatches.yaml"


@pytest.fixture(scope="module")
def documented():
    return yaml.safe_load(EXPECTED.read_text())


def test_all_twelve_pairs_exist(reference_repo):
    pairs = benchmark_pairs(reference_repo)
    assert len(pairs) == 12
    for _, y, j in pairs:
        assert y.is_file() and j.is_file(), (y, j)


def test_every_mismatch_is_documented_and_reviewed(reference_repo, documented):
    cats = documented["categories"]
    entries = documented["mismatches"]
    for e in entries:
        assert e["category"] in cats, f"unreviewed or unknown category: {e}"
    computed = {m.key for m in compare_all(reference_repo)}
    listed = [(e["workflow"], e["kind"], e["node"], e["detail"]) for e in entries]
    assert len(listed) == len(set(listed)), "duplicate entries in expected_mismatches.yaml"
    new = computed - set(listed)
    stale = set(listed) - computed
    assert not new, "undocumented mismatches:\n" + "\n".join(map(str, sorted(new)))
    assert not stale, "documented mismatches that no longer occur:\n" + "\n".join(map(str, sorted(stale)))


def test_no_mismatch_is_blamed_on_flowcheck(documented):
    # A FlowCheck bug gets fixed, not documented.
    assert not [e for e in documented["mismatches"] if e["category"] == "FlowCheck-bug"]


def test_summary_is_stable(documented):
    counts = Counter(e["category"] for e in documented["mismatches"])
    assert sum(counts.values()) == len(documented["mismatches"])
