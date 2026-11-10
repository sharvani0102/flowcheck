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
- FC004 isolated-context (warning): fixed phrase list and fixtures, added
  before FC004 was run on any benchmark workflow.

- `mutate.py` and Step 4b in `evaluate.py`: six mutation types injected into
  the passed workflows, with recall per type.
- Step 4c in `evaluate.py`: every finding on AgentSPEX's example workflows,
  reviewed in `results/annotations/agentspex_review.yaml`.
- README: install, usage, rules, FC004 caveats, results, limitations, gaps and
  next steps, how FlowCheck fits in front of Lean4Agent's pipeline, library
  usage and extension points, reproduction steps, and credits.

### Changed
- Step 4a re-run with FC004; annotations, results doc and README numbers updated.
- FC004 quotes the earliest matching phrase in the instruction (presentation
  only; what fires is unchanged).
- README rule table: FC003 covers list-form `parallel` and modules only (loops
  and branches are not scopes in AgentSPEX); FC005 added.
