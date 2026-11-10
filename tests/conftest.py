from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
REFERENCE = ROOT / "reference" / "Lean4Agent"


@pytest.fixture(scope="session")
def reference_repo() -> Path:
    if not REFERENCE.is_dir():
        pytest.skip("reference/Lean4Agent is not cloned (see README)")
    return REFERENCE
