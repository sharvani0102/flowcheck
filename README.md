# FlowCheck

**A static checker for AI agent workflows.** FlowCheck finds the steps in a workflow that
depend on information they were never given, before you spend a single LLM call running it.

## The problem
An agent workflow is a sequence of LLM steps: each step reads variables that earlier steps
wrote. When a step reads something no earlier step produced, nothing crashes. AgentSPEX
sends the unresolved `{{variable}}` placeholder to the model as literal text, and the model
guesses. You pay for the whole run and get a worse answer without knowing why.

FlowCheck is pure Python, deterministic, and makes no LLM calls. It checks all 28 workflows
used in its evaluation in about 0.3 seconds.

## Install
Python 3.10 or newer.

```bash
git clone https://github.com/sharvani0102/flowcheck
cd flowcheck
python -m venv venv
venv/bin/pip install -e '.[dev]'
```

The only runtime dependency is PyYAML; pytest is for development.

On macOS, name the virtual environment `venv/` rather than `.venv/`. macOS can set the
"hidden" flag on files inside dot-directories, and Python 3.12 skips hidden `.pth` files,
which breaks an editable install.

## Usage
```bash
flowcheck path/to/workflow.yaml [more files or directories] [--format text|json] [--rules FC001,FC004]
```

Directories are searched for `*.yaml` / `*.yml`. Exit code 0 means no errors, 1 means errors
were found, and 2 means at least one file could not be parsed. Warnings don't change the
exit code.

Excerpt of the output for one of Lean4Agent's rejected workflows:

```
$ flowcheck experiments/SWE_bench_verified/failed_workflow_3.yaml
...
experiments/SWE_bench_verified/failed_workflow_3.yaml:109: warning FC004 [candidate_file_list (task)] var 'candidate_files_report': its instruction names 'candidate_files_report' in plain text, referring to earlier work, but this is a task, which has no conversation history ...
    hint: pass the earlier result explicitly, e.g. {{candidate_files_report}}
experiments/SWE_bench_verified/failed_workflow_3.yaml:184: error FC005 [switch (no variable) (switch)] this switch has no string 'variable' key, so AgentSPEX looks up None, no case ever matches, and it always runs 'default' ...
    hint: use 'variable: <name>' (quote YAML keys like 'on')
...
```

`--format json` prints every finding with its rule, severity, node, line, variable,
message and hint. Full sample output is in [results/demo.txt](results/demo.txt).

## What FlowCheck checks
**Error** means the problem is certain from the YAML. **Warning** means it is heuristic, or
depends on a path FlowCheck can't prove is taken.

| Rule | Severity | Catches |
|---|---|---|
| FC001 undefined-read | error | A step reads a variable that no reachable earlier step writes and that isn't a parameter or built-in. This includes `{{ x }}` with spaces, which AgentSPEX never substitutes, and a dotted `for_each: in: a.b`, which it looks up as one flat key. |
| FC002 maybe-undefined | warning | A variable is written on some paths to the reader but not all, e.g. only in one branch, or only inside a loop that may run zero times. |
| FC003 scope-leak | error | A value written inside a list-form `parallel` block, a sibling parallel branch, or a called module is read outside it without being exported (`save_as` / `save_results_as`). |
| FC004 isolated-context | warning | A node that can't see earlier work refers to it: a `task`, or a `step` with no earlier `step` in its conversation history, that receives no earlier output through `{{...}}`. |
| FC005 runtime-trap | error | A construct AgentSPEX silently mis-executes: an unsupported step type (skipped), a `switch` without a `variable` (always `default`), a condition outside the supported grammar such as `contains` (always false), a module-form `parallel` with no `max_workers` (crashes). |

FC001–FC004 are the original phase-one spec. FC005 was added after reading the AgentSPEX
source turned up these traps.

Loops and `if`/`switch` branches are not scopes in AgentSPEX: they share one context, so a
value written inside them is still visible afterwards. FlowCheck reports those cases only
when some path skips the write (FC002). Every rule follows the runtime semantics written
up, with source citations, in [docs/agentspex-semantics.md](docs/agentspex-semantics.md).

### FC004 is a heuristic
FC004 looks for a small, fixed list of phrases that refer to earlier work ("your fix", "the
previous", "you found", "above", ...; the full list is in
[`checks/fc004_isolated_context.py`](src/flowcheck/checks/fc004_isolated_context.py)). It
also looks for an upstream variable named in plain text, such as `Source: question_analysis`,
without being templated. The list was committed before FC004 was run on any benchmark.

Expect both kinds of mistake:
- **Missed references.** Wording outside the list ("Now that you've explored the
  repository") is not recognised. In mutation testing FC004 found 7 of 12 injected cases.
- **False alarms.** It can't tell a backward reference from a forward one ("verify your fix
  works", in a step's own to-do list).
- **Tools as a side channel.** A task can read files earlier steps wrote (`fs_read`, shell,
  `git diff`), which a static check can't see.

## Results (phase one)
Full tables, generated from `results/phase1_eval.json`:
[docs/phase1-results.md](docs/phase1-results.md).

**Lean4Agent's 12 labelled workflows** (6 rejected by Lean4Agent, 6 accepted):

| Rules counted | Rejected workflows flagged | Accepted workflows flagged |
|---|---|---|
| Errors only (certain), FC001–FC005 | 3 of 6 | 1 of 6 |
| Errors or warnings, FC001–FC005 | 6 of 6 | 4 of 6 |
| Errors only, FC001–FC004 | 1 of 6 | 1 of 6 |

- **Two of the four flagged "accepted" workflows contain real bugs Lean4Agent misses.**
  - ELAIP-Bench `passed_workflow_3` loops over `in: question_keywords.keywords`. AgentSPEX looks
    that up as one flat key, so the loop runs once over the literal string (FC001).
  - ELAIP-Bench `passed_workflow_1` has a task that says "Source: question_analysis" but never
    receives it (FC004).
- **The other two are false positives (both warnings).**
  - SWE-Bench `passed_workflow_3`: a value written inside a `while` loop that always runs at
    least once (FC002; FlowCheck is path-insensitive).
  - SWE-Bench `passed_workflow_2`: "your fix" in a first step's to-do list (FC004).
- **Bugs Lean4Agent doesn't report.** Two rejected SWE-Bench workflows contain `contains`
  conditions that are always false and a `switch: on:` that YAML reads as a boolean, so whole
  branches can never run (FC005).
- **Lean4Agent's documented problems.** Of the 17 problems Lean4Agent lists for its rejected
  workflows, FlowCheck catches 6. 8 need per-step annotations (phase two), and 3 are
  FlowCheck gaps (loop steps whose output is never saved).

**Mutation testing** (bugs injected into the 6 accepted workflows):

| Mutation | Recall |
|---|---|
| delete the producer of a later-read variable (FC001) | 18 of 18 |
| rename a read to an unwritten name (FC001) | 101 of 101 |
| move a reader before its writer (FC001) | 13 of 13 |
| wrap a producer in `parallel` with no export (FC003) | 18 of 18 |
| turn a history-dependent `step` into a `task` (FC004) | 7 of 12 |
| remove a write from one branch (FC002) | no applicable site in these workflows |

Structural recall is high by construction; the FC004 row measures its phrase heuristic.

**AgentSPEX's own example workflows** (16 files): 25 findings, all reviewed as real issues.
Most of them are steps of types the current interpreter has dropped (`save`, `synthesize`,
`discover`, `evaluate`), which it silently skips. Reviewing them turned up one issue
FlowCheck misses: a non-integer `for_each` `limit`, which crashes.

**Graph cross-check.** FlowCheck's parsed graph was compared with Lean4Agent's IR for all
12 workflows. All 103 differences are reviewed in
[tests/crosscheck/expected_mismatches.yaml](tests/crosscheck/expected_mismatches.yaml), and
none is a FlowCheck error. They come from Lean4Agent's IR being linearised or hand-abstracted,
and from two IR files that are stale relative to their YAML.

## Limitations
- **Path-insensitive.** FlowCheck assumes every branch can be taken and every loop can run
  zero times. That causes FC002 false positives when a loop always runs at least once. The
  analysis has a `FeasibilityOracle` hook ([dataflow.py](src/flowcheck/dataflow.py)) for a
  phase-two Z3 check to rule out infeasible paths.
- **Variables, not fields.** `{{a.b.c}}` is checked as a read of `a`. Whether the value has a
  field `b`, or matches a JSON schema, is out of scope for phase one.
- **No per-step pre/postconditions.** Problems about what a step's output means ("the
  evidence isn't grounded", "the verification is stale") need annotations like Lean4Agent's.
- **Modules are checked one file at a time.** A module's declared `parameters` are treated
  as defined. Module internals are read only to explain FC003 findings in the caller.
- **Not modelled:** checkpoint resume, `max_workflow_steps` truncation, the runtime's
  continue-after-error behaviour, and work passed between steps through files.
- **Not yet checked:** outputs that are produced and never used (e.g. a loop step with no
  `save_as`), and non-integer `for_each` `limit` values.

## Reproducing the evaluation
The evaluation and the cross-check test read Lean4Agent as reference material:

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/RickySkywalker/Lean4Agent reference/Lean4Agent
venv/bin/pytest                      # unit tests, rule fixtures, graph cross-check
venv/bin/python scripts/evaluate.py  # writes results/phase1_eval.json, docs/phase1-results.md, results/demo.txt
```

The results in this repository were produced against Lean4Agent commit `e6a78cb`.
`results/annotations/` holds the hand-written reviews (why each Lean4Agent problem is or
isn't caught, and a verdict on each finding). `evaluate.py` fails if a review is missing,
or if it claims a finding that FlowCheck doesn't actually report.

## Relationship to Lean4Agent
[Lean4Agent](https://github.com/RickySkywalker/Lean4Agent) (MIT) formally verifies agent
workflows in Lean 4, which requires an LLM-written specification and a Lean build for each
workflow. FlowCheck is a lightweight first pass: it catches the information-flow bugs that
are visible from the YAML alone, in under a second, so a broken workflow can be fixed before
full verification. It is evaluated against Lean4Agent's published verdicts.

FlowCheck doesn't include Lean4Agent code. It mirrors two of AgentSPEX's regular expressions
so it parses templates and conditions exactly as the runtime does; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License
MIT, see [LICENSE](LICENSE). Lean4Agent is MIT-licensed, © 2026 Ruida Wang.
