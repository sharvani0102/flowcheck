# Changelog

All notable changes to FlowCheck are listed here.

## [Unreleased] - phase one

### Added
- Project scaffold: `pyproject.toml`, MIT license, third-party notices.
- `docs/agentspex-semantics.md`: AgentSPEX execution semantics with source citations.
- Parser from AgentSPEX YAML to a control-flow-graph IR (`ir.py`, `parser.py`,
  `yamlload.py`, `templates.py`), with line numbers.
- Dataflow solver (may / must / may-without-discards) with a `FeasibilityOracle`
  hook for a future path-sensitive (Z3) check (`dataflow.py`).
- Checks FC001 undefined-read, FC002 maybe-undefined, FC003 scope-leak, and
  FC005 runtime-trap (an addition to the original spec).
- `flowcheck` CLI with text and JSON output and exit codes 0/1/2.
- Tests: rule fixtures, parser and CLI tests, and an exact graph cross-check
  against Lean4Agent's 12 benchmark IR files with every mismatch reviewed in
  `tests/crosscheck/expected_mismatches.yaml`.
- `scripts/evaluate.py` (Step 4a): runs FlowCheck on Lean4Agent's 12 labelled
  workflows, validates the hand-written review in
  `results/annotations/lean4agent_problems.yaml`, and writes
  `results/phase1_eval.json`, `docs/phase1-results.md` and `results/demo.txt`.

### Changed
- README rule table: FC003 covers list-form `parallel` and modules only (loops
  and branches are not scopes in AgentSPEX); FC005 added.
