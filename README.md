# FlowCheck

**A static checker for AI agent workflows.** FlowCheck finds the steps in a workflow that
depend on information they were never given, before you spend a single LLM call running it.

## The problem
An agent workflow is a sequence of LLM steps: each step reads variables that earlier steps
wrote. When a step reads something no earlier step produced, nothing crashes. AgentSPEX
sends the unresolved `{{variable}}` placeholder to the model as literal text, and the model
guesses. You pay for the whole run and get a worse answer without knowing why.

## What FlowCheck checks
| Rule | Catches |
|---|---|
| FC001 undefined-read | A step reads a variable that no reachable earlier step writes |
| FC002 maybe-undefined (warning) | A variable is written on some paths to the reader but not all (e.g. only in one branch, or inside a loop that may run zero times) |
| FC003 scope-leak | A value written inside a list-form `parallel` block or a called module is read outside it without being exported (`save_as` / `save_results_as`) |
| FC004 isolated-context (warning) | An isolated `task` step refers to earlier work it can't see |
| FC005 runtime-trap | A construct AgentSPEX silently mis-executes: an unsupported step type, a `switch` without a `variable`, a condition outside the supported grammar |

Loops and `if`/`switch` branches are not scopes in AgentSPEX: they share one context, so a
value written inside them is still visible afterwards. FlowCheck reports those cases only
when some path skips the write (FC002). See
[docs/agentspex-semantics.md](docs/agentspex-semantics.md) §5.

FlowCheck is pure Python, deterministic, and makes no LLM calls.

## Relationship to Lean4Agent
[Lean4Agent](https://github.com/RickySkywalker/Lean4Agent) (MIT) formally verifies agent
workflows in Lean 4, which requires an LLM-written specification and a Lean build for each
workflow. FlowCheck is a lightweight first pass: it catches the information-flow bugs that
are visible from the YAML alone, in seconds, so a broken workflow can be fixed before full
verification. It is evaluated against Lean4Agent's published verdicts.

## Status
Phase 1 (in progress): AgentSPEX parser, rules FC001–FC005, and evaluation on Lean4Agent's
12 benchmark workflows. See [docs/phase1-results.md](docs/phase1-results.md).

## Results so far (FC001–FC003, FC005; FC004 not yet implemented)
On Lean4Agent's 12 labelled workflows (6 rejected by Lean4Agent, 6 accepted), FlowCheck
checks all of them in under half a second.

| Rules counted | Rejected workflows flagged | Accepted workflows flagged |
|---|---|---|
| Errors, FC001–FC005 | 3 of 6 | 1 of 6 |
| Errors or warnings, FC001–FC005 | 5 of 6 | 2 of 6 |
| Errors, FC001–FC004 only | 1 of 6 | 1 of 6 |

- **Bugs Lean4Agent doesn't report.** Two rejected SWE-Bench workflows contain constructs
  AgentSPEX silently mis-executes: `contains` conditions that are always false, and a
  `switch: on:` that YAML reads as a boolean, so the switch always takes `default` (FC005).
- **A bug in a workflow Lean4Agent accepts.** ELAIP-Bench `passed_workflow_3` loops over
  `in: question_keywords.keywords`. AgentSPEX looks that up as one flat key, so the loop runs
  once over the literal string (FC001).
- **One false positive.** SWE-Bench `passed_workflow_3` gets an FC002 warning for a value
  written inside a `while` loop that always runs at least once. FlowCheck is
  path-insensitive; a phase-two path check would remove it.
- **Lean4Agent's documented problems.** Of the 17 problems Lean4Agent lists for its rejected
  workflows, FlowCheck catches 1 so far. 8 need per-step annotations (phase two), and 8 are
  gaps, mostly the isolated-context cases FC004 targets.
- **Graph cross-check.** FlowCheck's parsed graph was compared with Lean4Agent's IR for all
  12 workflows. All 103 differences are reviewed, and none is a FlowCheck error. They come
  from Lean4Agent's IR being linearised or hand-abstracted, and from two IR files that are
  stale relative to their YAML.
