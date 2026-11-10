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
| FC002 maybe-undefined | A variable is written on some paths to the reader but not all |
| FC003 scope-leak | A value written inside a parallel/loop/module scope is read outside it |
| FC004 isolated-context (warning) | An isolated `task` step refers to earlier work it can't see |

FlowCheck is pure Python, deterministic, and makes no LLM calls.

## Relationship to Lean4Agent
[Lean4Agent](https://github.com/RickySkywalker/Lean4Agent) (MIT) formally verifies agent
workflows in Lean 4, which requires an LLM-written specification and a Lean build for each
workflow. FlowCheck is a lightweight first pass: it catches the information-flow bugs that
are visible from the YAML alone, in seconds, so a broken workflow can be fixed before full
verification. It is evaluated against Lean4Agent's published verdicts.

## Status
Phase 1 (in progress): AgentSPEX parser, rules FC001–FC004, and evaluation on Lean4Agent's
12 benchmark workflows. See [docs/phase1-results.md](docs/phase1-results.md).
