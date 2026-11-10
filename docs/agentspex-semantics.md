# AgentSPEX workflow semantics, as FlowCheck models them

This document records how the AgentSPEX runtime actually executes a YAML
workflow. Every rule cites the source line it comes from. FlowCheck's parser
and checks implement these rules and nothing else; where the runtime is
ambiguous, the choice FlowCheck makes is listed in
[§12](#12-interpretation-choices-flowcheck-makes).

**Source version.** Lean4Agent commit `e6a78cb` (2026-07-28), cloned to
`reference/Lean4Agent`. Unless a path says otherwise it is relative to
`reference/Lean4Agent/AgentSPEX/src/harness/`. "Docs" means
`AgentSPEX/docs/workflow-language.md`.

---

## 1. Loading a workflow

1. The file is read as text. `${VAR}` and `${VAR:-default}` are replaced from
   the process environment *before* YAML parsing
   (`parsing/yaml_parser.py:22-36`, `:38-74`). An unset `${VAR}` with no
   default is left as the literal text `${VAR}` (`:48`).
2. The text is parsed with `yaml.safe_load` (`yaml_parser.py:31`), which
   follows YAML 1.1 (see [§9](#9-yaml-pitfalls)).
3. `name`, `goal` and `workflow` must exist and `workflow` must be a list, or
   loading raises `ValueError` (`yaml_parser.py:76-84`).
4. The runtime then executes the **raw YAML dicts** in `yaml_data["workflow"]`
   (`agent.py:224, 247`). The `_parse_*` helpers in `yaml_parser.py:140-269`
   are not called anywhere in the harness, and they drop `save_as`, so they are
   not a description of runtime behaviour.

The same loader is used for sub-modules (`execution/step_handlers/calls.py:183, 194`).

## 2. Execution model

- **One context dict.** All state lives in a single `context` dict that is
  passed by reference to every step handler (`agent.py:562-573`,
  `execution/interpreter.py:132-140`). Variables are keys in this dict.
- **Top-level loop.** Top-level steps run in order (`agent.py:646-711`). After
  each top-level step, `context["prev_output"]` is set to that step's output
  (`agent.py:711`).
- **Errors don't stop the workflow.** If a top-level step raises, the error is
  logged and the loop continues with the next top-level step
  (`agent.py:778-787`). The failing step's `save_as` is never written.
- **`max_workflow_steps`** (config) stops the top-level loop early
  (`agent.py:692-696`).
- **Dispatch by key.** The step kind is the first key of the step mapping that
  is in a fixed set: `step task for_each if parallel while switch increment
  set_variable input call gather return` (`interpreter.py:191-206`), then
  `_dispatch_step` routes on key presence (`interpreter.py:280-359`).
- **Unknown kinds are silent no-ops.** Any other mapping key is logged as
  "Unknown step type" and returns `("", 0)`. No error is raised and nothing is
  written (`interpreter.py:360-362`).
  - This covers `save`, `synthesize`, `discover`, `evaluate` and `validate`,
    which the old engine supported. Lean4Agent records that these were removed
    (`FormalAgentLib/AgentVerifier/YamlStepType.lean:57-58`;
    `leanagent/codegen/lean_verifier/yaml_parser.py:176`). Several example
    workflows in `AgentSPEX/workflows/` still use them.
- **`plan_executor.py` behaves the same.** `agentic_loop/plan_executor.py` is a
  second runner for generated plans. It builds the same context
  (`plan_executor.py:191-208`) and uses the same `Interpreter`
  (`plan_executor.py:251-262`), so everything here applies to it too.

## 3. Node types

For each kind: which fields are template-expanded (so `{{...}}` in them is a
read), which bare names it looks up, what it writes, and how control flows.

### 3.1 `step` (LLM call with conversation history)

- **Templated:** `name` (`basic.py:65-66`), `instruction` (`basic.py:67-68`),
  `save_as` (`basic.py:102`).
- **Ignored:** a per-step `system_prompt` is logged as ignored
  (`basic.py:72-77`). The docs claim it works (`workflow-language.md:122-136`);
  the code says otherwise.
- **History.** The expanded instruction is appended as a user message to the
  workflow-level history (`basic.py:73, 79`). The LLM runs on that history,
  with tools (`basic.py:82-98`). The model's turns and any inline tool results
  are appended to the same history (`execution/llm_executor.py:727-728,
  736-737`, and the same pattern further down in `multi_step_tool_call_loop`).
- **Writes:** `save_as` if present. The output is passed through `eval()`, and
  falls back to the raw string if that fails (`basic.py:100-107`).
- **Control:** sequential.

### 3.2 `task` (stateless LLM call)

- **Templated:** `name` (`basic.py:142-143`), `instruction` (`basic.py:144-145`),
  `save_as` (`basic.py:165`), and a per-task `system_prompt`, which is honoured
  here and template-expanded (`basic.py:146`, `llm_executor.py:253-254`).
- **Messages:** exactly `[system, user(instruction)]` (`llm_executor.py:263-268`).
  The `prev_output` argument is accepted but unused
  (`llm_executor.py:223`, marked `noqa: ARG002`).
- **Writes:** `save_as`, same `eval` rule (`basic.py:163-170`).
- **Control:** sequential.

### 3.3 `set_variable`

- **Templated:** `value`, after `str()` (`variables.py:92`). The result is then
  `eval()`'d, falling back to the string (`variables.py:93-96`).
- **Not templated:** `name` is used verbatim (`variables.py:88`).
- **Reads:** if `value` is absent it reads `prev_output` (`variables.py:89`).
- **Writes:** `name` (`variables.py:105`).

### 3.4 `increment`

- **Form:** `increment: var_name`, a bare string (`variables.py:54-56`).
- **Reads:** `var_name`, but a missing or non-integer value is treated as 0
  (`variables.py:57-60`). Incrementing an undefined variable is therefore *not*
  an error at runtime.
- **Writes:** `var_name` (`variables.py:61`).

### 3.5 `input`

- **Templated:** `prompt` (`variables.py:212-214`).
- **Writes:** `save_as`, which defaults to `user_input` and is **not** templated
  (`variables.py:215, 422`). Outside a terminal it uses `default`
  (`variables.py:345-364, 413-415`).

### 3.6 `return`

- **Form:** `return: var_name`. A missing or empty value means `prev_output`
  (`variables.py:148-152`).
- **Templated:** the variable name (`variables.py:155`).
- **Reads:** that variable. If it is not in the context, the *name itself* is
  returned as a literal string (`variables.py:162-167`).
- **Writes:** nothing directly. The returned value becomes the step output, so
  it lands in `prev_output` via the caller (`agent.py:711`, `calls.py:321`).
- **Not an early exit.** Neither the module loop (`calls.py:293-339`) nor the
  top-level loop (`agent.py:646-787`) breaks on `return`, so steps after it
  still run. `stop_on_return` only affects the LLM tool loop
  (`llm_executor.py:670-675`).

### 3.7 `if`

- **Templated:** `condition`, expanded before evaluation (`control.py:250-251`).
- **Reads:** condition operands (see [§8](#8-conditions)).
- **Control:** runs `then` or `else`. A missing `else` is `[]`
  (`control.py:247-248`).
- **Context:** both branches run on the **same** context dict
  (`control.py:276-283`), and `prev_output` is updated after each sub-step
  (`control.py:288`).

### 3.8 `while`

- **Templated:** `condition`, re-expanded and re-evaluated **before every
  iteration, including the first** (`control.py:336-338`). The loop may run
  zero times.
- **Limit:** `max_iterations` defaults to 25 (`control.py:327`). The docs'
  quick reference and the legacy parser say 10; the code says 25.
- **Context:** the body runs on the same context dict (`control.py:354-361`),
  and `prev_output` is updated per sub-step (`control.py:366`).

### 3.9 `for_each`

- **Fields:** `variable`, `in`, `steps`, optional `limit` (`control.py:157-161`).
  None of them are templated.
- **What it iterates over**:
  - If `in` is a string, it is looked up **flat**:
    `context.get(in, [in])` (`control.py:167-169`). There is no dotted-path
    resolution and no `{{}}` expansion. So `in: a.b` or `in: "{{a}}"` iterates
    once over the literal string, and an undefined name also iterates once
    over the literal name.
  - If `in` is a YAML list, that list is used as-is (`control.py:170-171`).
  - Any other value becomes `[str(in)]` (`control.py:172-173`).
- **`limit`** slices the list (`control.py:175-176`). The docs show
  `limit: "{{breadth}}"` (`workflow-language.md:1396`), but `limit` is not
  templated, and slicing a list with a string raises `TypeError`.
- **Writes:** the loop variable, on each iteration, into the **shared** context
  (`control.py:185-186`, where `iter_context = context` is not a copy). It is
  still set to the last item after the loop.
- **Zero iterations:** happens when the list is empty.

### 3.10 `switch`

- **Fields:** `variable`, `cases`, `default` (`control.py:404-407`).
- **Reads:** `variable`, looked up flat with `context.get(variable)`
  (`control.py:410`). The comment "after template expansion" at
  `control.py:409` is wrong: no expansion happens.
- **Control:** the case is chosen by exact dict lookup `cases.get(value)`,
  which is type-sensitive; otherwise `default` runs, and a missing `default` is
  `[]` (`control.py:415-417`).
- **Context:** shared (`control.py:431-438`).

### 3.11 `call` (sub-module)

- **Templated:** `module` path (`calls.py:113`), each parameter value after
  `str()` (`calls.py:176-178`), and `save_as`, expanded in the parent
  (`calls.py:346`).
- **Path resolution:** paths starting with `modules/` or `workflows/` resolve
  against `workflow_root`, other relative paths against `workflow_dir`, with a
  fallback to the current directory (`calls.py:114-137`).
- **Parameters as env vars:** passed parameters are also exported as UPPERCASE
  environment variables while the module file is loaded, so `${PARAM}` in the
  module text picks them up (`calls.py:185-190, 194`).
- **Isolated context:** the module runs in a brand-new `sub_context`
  (`calls.py:226-243`), seeded with:
  - the module's own `parameters`, then overridden by the passed ones
    (`calls.py:245-250`);
  - the parent's `prev_output` (`calls.py:231`);
  - config keys (`calls.py:257-280`);
  - a **fresh** conversation history (`calls.py:286`).
- **Result:** `sub_context.get(return_var, sub_context["prev_output"])`, where
  `return_var` is the call's `return:` key and defaults to `prev_output`
  (`calls.py:140-142, 342`).
- **Writes (parent):** `save_as`, if given (`calls.py:345-348`). Nothing else
  from the module's context reaches the parent.
- **Recursion limit:** 10 by default (`calls.py:157-173`).

### 3.12 `gather` (parallel calls)

- **Fields:** `calls` (a list of call configs), `save_results_as`,
  `max_workers` (`calls.py:473-476`).
- **Execution:** each call runs `execute_call_step` on
  `copy.deepcopy(context)` in a thread pool (`calls.py:505-517, 587-592`).
- **Writes (parent):**
  - each call's `save_as` (expanded against the parent context), or
    `gather_result_{i+1}` when there is no `save_as` (`calls.py:519-523,
    606-608`);
  - `save_results_as`, a dict keyed by those names (`calls.py:619-622`).
- **Failed calls** still write their error message under the same name
  (`calls.py:552-579`).

### 3.13 `parallel`

Two forms (`calls.py:680-745`).

- **Module form:** `parallel: {module, parameters_list, save_results_as,
  max_workers}`.
  - Delegates to `gather` with one call per parameter dict
    (`calls.py:683-720`). A string `parameters_list` is expanded, has its
    braces stripped, and is looked up in the context (`calls.py:705-710`).
  - **Writes:** `result_1 … result_N` and `save_results_as`
    (`calls.py:462-470, 606-622`). `save_as_list` is passed along but never
    read (`calls.py:693` vs `:451-476`).
  - **`max_workers`** is evaluated as `int(context.get(mw, mw))`, so a
    variable name or a number works, but **omitting it raises `TypeError`**
    (`int(None)`) (`calls.py:695-700`).
- **List form:** `parallel: [step, step, ...]`.
  - Each child runs on its own `copy.deepcopy(context)`, and the copies are
    discarded (`calls.py:730-745`). **No write made inside a list-form
    parallel child survives**, and siblings never see each other's writes.
  - The children actually run one after another (comment at `calls.py:722`).
  - The only thing that comes out is the combined text output, which becomes
    `prev_output` of the enclosing level.

### 3.14 Submodules exposed as tools

A workflow can declare `submodules:` and let the LLM call them as functions
(docs `workflow-language.md:976-1042`; `submodules/loader.py:14-38`). The tool
call is turned into a `call` with `return: <function.return>`
(`submodules/invoker.py:54-65`), and context parameters are copied from the
caller's context (`invoker.py:30-37`). The result comes back as a tool
message in the conversation, not as a workflow variable, so it is invisible
to the dataflow checks.

## 4. What each node can see

| Node | Conversation history | Variables |
|---|---|---|
| `step` | the shared workflow history: system prompt + every earlier `step`'s instruction and model turns in the same history scope ([§3.1](#31-step-llm-call-with-conversation-history)) | whatever is substituted into its instruction |
| `task` | none (`llm_executor.py:263-268`) | whatever is substituted into its instruction and its `system_prompt` |
| others | no LLM call | their own fields |

Consequences:

- **Tasks are invisible to steps.** `task`s never write to the history, so a
  `step` that follows only `task`s sees no earlier work in its history.
- **History scopes.** A module gets a fresh history (`calls.py:286`). A
  list-form parallel child gets a deep copy that is thrown away
  (`calls.py:732`).
- **Tools are a side channel.** Both `step` and `task` can use tools such as
  `fs_read` or a shell, so they can read files that earlier steps wrote. A
  static check can't see that channel, which is why FC004 is a heuristic
  warning.
- **The system prompt** is the workflow's `system_prompt`, template-expanded
  (`prompts/yaml_prompts.py:29-31, 34-45`), or a default that embeds
  `task_name` and `goal` (`yaml_prompts.py:53-60`).
  - For `step`s it is expanded **once**, when the history is created at
    workflow start (`agent.py:619`, `execution/conversation.py:36-52`).
  - For `task`s it is expanded at every call (`llm_executor.py:255-261`).

## 5. Scoping

- **Control flow does not scope.** `if`, `switch`, `while` and `for_each` all
  run their bodies on the same context dict (`control.py:185-186, 276-283,
  354-361, 431-438`).
  - A variable written inside a branch or loop body is visible after it.
  - So is the `for_each` loop variable, holding the last item.
  - The only way such a read can fail is if the path skipped the write: the
    other branch was taken, or the loop ran zero times.
- **Scopes that discard writes**:
  - **List-form `parallel` children** (`calls.py:730-745`): nothing leaks out,
    and there is no export mechanism.
  - **Module bodies** (`call`, `gather`, module-form `parallel`): only the
    returned value comes back, and only into `save_as` / `gather_result_N` /
    `result_N` / `save_results_as` (`calls.py:342-348, 606-622`).
- **Exports, summarised:** `call.save_as`; `gather` per-call `save_as` (or
  `gather_result_N`) and `save_results_as`; module-form `parallel` `result_N`
  and `save_results_as`. A module chooses *which* of its variables is returned
  with the caller's `return:` key (default `prev_output`, which a final
  `- return: x` step sets to `x`).

## 6. Implicitly available names

Names that exist without any step writing them, so reading them is never an
undefined read.

- **Top-level workflow** (`agent.py:562-577, 586-626`):
  - always: `task_name`, `goal`, `task_output_dir`, `sandbox_output_dir`,
    `prev_output` (initially `""`), `submodule_tools`, `submodule_registry`,
    `workflow_file`, `workflow_dir`, `workflow_root`, `conversation_history`;
  - every key under `parameters:`;
  - `system_prompt`, if the workflow sets one;
  - the config keys listed at `agent.py:590-606`, if set in `config:` or at
    the top level;
  - `problem_statement`, only when the CLI passes `--problem_statement`
    (`agent.py:621-626`).
- **Module:** `task_name`, `goal`, `task_output_dir`, `sandbox_output_dir`,
  `prev_output` (inherited), `submodule_tools`, `submodule_registry`,
  `workflow_file`, `workflow_dir`, `workflow_root`, `_call_depth`,
  `_parent_context`, `stop_on_submodule_result`, `stop_on_return`, the
  module's `parameters`, the caller's passed parameters, `system_prompt` if
  set, and config keys (`calls.py:226-280`).
- **Loop variable:** inside a `for_each` body (`control.py:186`).
- **`user_input`:** after an `input` step with no `save_as` (`variables.py:215`).
- **Gather/parallel defaults:** `gather_result_N` / `result_N` after a gather
  or module-form parallel ([§3.12](#312-gather-parallel-calls),
  [§3.13](#313-parallel)).

## 7. Templates and dotted access

- **Pattern.** Placeholders match `\{\{([^}]+)\}\}` (`parsing/templates.py:23`).
- **Whitespace is not stripped.** The captured name is used as-is, so
  `{{ x }}` looks up the key `" x "` and never resolves
  (`templates.py:12-20`). Lean4Agent's own IR builder *does* accept the spaced
  form (`experiments/elaip_LeanEvolve/build_layer2_ir.py:213`), so the two
  disagree on that case.
- **Plain names** use `context.get(name, <original placeholder>)`
  (`templates.py:20`).
- **Dotted names** `{{a.b.c}}` go through `resolve_dotted_path`
  (`templates.py:15-19`, `parsing/conditions.py:50-81`):
  - It walks one segment at a time through dicts (`conditions.py:77-80`).
  - If the current value is a string, it is first parsed as JSON, then as a
    Python literal, after stripping a markdown code fence
    (`conditions.py:13-47, 71-75`). So `{{analysis.keywords}}` works when
    `analysis` holds JSON text.
  - Lists cannot be indexed (only `dict` membership is checked,
    `conditions.py:77`), so `{{items.0}}` never resolves.
- **Unresolved placeholders stay literal.** Anything unresolved is left as the
  literal `{{...}}` text and sent to the LLM without error
  (`templates.py:19-20`). This is the failure FlowCheck exists to catch.
- **Single pass.** `re.sub` does one pass, so a substituted value that itself
  contains `{{x}}` is not expanded again.
- **FlowCheck's model.** A read of `{{a.b.c}}` is a read of the root variable
  `a`. Field-level checking is phase two.

## 8. Conditions

`if.condition` and `while.condition` are template-expanded and then evaluated
by `evaluate_condition` (`control.py:251, 336-337`;
`parsing/conditions.py:215-252`).

- **`and`/`or`.** The string is split on `\s+or\s+`, then on `\s+and\s+`, so
  `and` binds tighter and there are no parentheses (`conditions.py:209-252`).
- **Atoms.** An atom must match
  `^(ident|number|quoted)\s*(==|!=|<=|>=|<|>)\s*(ident|number|quoted)$`, where
  `ident` may be dotted (`conditions.py:142-146`).
- **Operand resolution** (`resolve_operand`, `conditions.py:84-120`):
  - a quoted string is a literal (`:97-101`);
  - a dotted identifier is resolved as a path (`:105-108`);
  - a number is a number (`:111-117`);
  - **any other identifier is `context.get(name, name)`**: an undefined name
    silently becomes the string literal `"name"` (`:120`). So
    `status == DONE` compares against the literal `"DONE"` unless a variable
    `DONE` exists.
- **Comparison** is numeric when both sides convert to float, otherwise it is
  a string comparison (`conditions.py:155-196`).
- **Truthiness.** An atom that isn't a comparison is a truthiness test on a
  name or dotted path, and is `False` if the name is undefined
  (`conditions.py:198-206`).
- **Anything else is always `False`.** That includes unsupported operators
  like `contains`, `in`, `not`, and function calls (`conditions.py:206`).

## 9. YAML pitfalls

- **YAML 1.1 booleans.** `safe_load` parses `yes`, `no`, `on`, `off`, `true`
  and `false` as booleans, including as mapping keys.
  - SWE `failed_workflow_3.yaml` writes `switch: {on: "fix_strategy.strategy",
    ...}`. PyYAML turns the key `on` into `True`, so `sw.get("variable")` is
    `None`, `context.get(None)` is `None`, no case matches, and **the switch
    always takes `default`** (`control.py:405-417`).
  - The same applies to `cases` keys such as `yes:` / `no:`, which become
    booleans and never equal a string value.
- **Unset `${VAR}`.** It stays literal (`yaml_parser.py:48`), so a parameter
  like `problem_statement: "${PROBLEM_STATEMENT}"` is the literal text
  `${PROBLEM_STATEMENT}` when the variable isn't set. It is still *defined*.

## 10. Where the AgentSPEX docs disagree with the code

| Docs claim | Code | Where |
|---|---|---|
| Per-step `system_prompt` overrides for that step (`workflow-language.md:122-136`) | Ignored for `step`; honoured only for `task` | `basic.py:72-77, 146` |
| `limit: "{{breadth}}"` in a `for_each` (`:1396`) | `limit` is not templated; slicing with a string raises | `control.py:175-176` |
| `while` max iterations default 10 (legacy parser) | 25 | `control.py:327` vs `yaml_parser.py:218` |
| `switch` "resolve variable value from context (after template expansion)" | no expansion | `control.py:409-410` |
| `return` "returns a value from a submodule" (implies stopping) | does not stop execution | `calls.py:293-339` |

## 11. Lean4Agent's own graph representation (for the cross-check)

- **ELAIP-Bench IR** (`FormalAgentLib/VerificationExamples/elaipbench_layer2/`)
  is produced mechanically by `experiments/elaip_LeanEvolve/build_layer2_ir.py`:
  - Nodes are a pre-order flattening of `step`, `task`, `set_variable`,
    `increment`, `discover` and `synthesize` entries. Loops, ifs and switches
    become a `branch_path` tag rather than nodes (`build_layer2_ir.py:225-322`).
  - Reads are `{{name}}` roots from `instruction` only, using a
    whitespace-tolerant regex (`:208-220, 243`). `set_variable` values and
    conditions are not scanned (`:292, 304`).
  - Writes are `save_as`, or the variable for `set_variable` / `increment`
    (`:241, 290, 302`).
  - Edges are a plain `seq` chain over the flattened list, even across
    branches and loops (`:394-399`).
- **SWE-Bench IR** (`FormalAgentLib/VerificationExamples/layer2_v2_ir/`) is a
  hand- or LLM-authored abstraction:
  - Nodes carry typed `reads` / `writes` plus pre- and postconditions.
  - Control nodes are `conditional`, `whileLoop`, `forEachLoop` and
    `switchBranch`, with `branch`, `loop`, `loopBack` and `switch` edges
    (`leanagent/codegen/lean_verifier/predicate_docs/ir_json_format.md`,
    `node_types.md`).
  - It omits scaffolding steps such as `set_variable` / `increment` and may
    rename or merge nodes. For example, `passed_workflow_3` has 11 IR nodes
    for 20 YAML steps.
- **Step type names** follow the current engine: `step` keeps history and
  `task` is stateless (`FormalAgentLib/AgentVerifier/YamlStepType.lean:51-52`).
  Some prose comments in the Lean examples still use the old, swapped meaning
  (`YamlStepType.lean:57`). For example, `SWE-Bench/failed_workflow_1.lean`
  says "ALL nodes are `step` type (fresh context each time)" about a YAML file
  made of `task`s.

## 12. Interpretation choices FlowCheck makes

The runtime is unambiguous about most of the above. Where FlowCheck has to
choose, it chooses as follows.

1. **Steps complete normally.** The error-continuation path
   (`agent.py:778-787`), checkpoint resume (`agent.py:579-584`) and
   `max_workflow_steps` truncation are not modelled.
2. **`problem_statement` and the config keys** are treated as always
   available, even though they are injected only in some runs. This avoids
   false positives.
3. **Loops may run zero times.** Every `while`, and every `for_each` that isn't
   over a non-empty YAML list literal, may run zero times
   (`control.py:167-176, 336-338`). Proving that a particular loop runs at
   least once needs path-sensitive reasoning, which is phase two (the Z3
   oracle).
4. **Bare identifiers in conditions.** A comparison operand that is never
   defined anywhere in the workflow is a string literal, as at runtime
   (`conditions.py:120`), and is not reported. A comparison operand that *is*
   written somewhere, or any `{{...}}` in a condition, is a real read.
5. **Never-defined names in truthiness tests and `return`** are reported as
   warnings, not errors, because the runtime gives them a defined (if probably
   unintended) meaning: `False` and the literal name, respectively
   (`conditions.py:203-206`, `variables.py:165-167`).
6. **`increment`** never produces an undefined-read finding, because the
   runtime defaults the value to 0 (`variables.py:57-60`).
7. **Templated `save_as`** such as `analysis_{{topic}}` may define any name
   matching the pattern, so a matching read is at best "maybe defined". It
   never satisfies a definite-assignment requirement. The same applies to
   `result_1 … result_N` after a module-form `parallel`: they exist only for
   the entries `parameters_list` actually has (`calls.py:462-469`), so a read
   of `result_1` is "maybe defined" (FC002), while `save_results_as` is
   always defined.
8. **Workflow-level `system_prompt` reads** are checked at workflow entry,
   which is when they are expanded for `step` history (`agent.py:619`).
9. **Module files analysed on their own** treat their declared `parameters`
   and module built-ins as defined. A caller's undeclared extra parameters are
   not visible. Module internals are loaded only to explain FC003 findings in
   the caller.
10. **`{{...}}` in a field that is used but not templated** (`for_each.in`,
    `switch.variable`, `set_variable.name`, `input.save_as`) is a read of the
    literal string. It never resolves at runtime.

## 13. How this maps to FlowCheck's IR

| YAML | FlowCheck node(s) | Edges |
|---|---|---|
| `step` / `task` / `set_variable` / `increment` / `input` / `return` / `call` | one node | `seq` |
| `if` | `if` header (reads condition) → then-body / else-body → `join` | header→then `branch_true`, header→else `branch_false`, bodies→join `seq` |
| `switch` | `switch` header → one body per case and default → `join` | header→case `branch_true` (label = case), header→default `branch_false` (label = `default`) |
| `while` / `for_each` | loop header (reads condition / `in`; `for_each` header writes the loop var on the body edge) → body | header→body `branch_true`, body end→header `loop_back`, header→after `branch_false` |
| `parallel` (list) | `fork` → one child region each → `join` (restores the fork's state) | `parallel_fork`, `join` |
| `gather` / `parallel` (module) | one node (writes the exported names) | `seq` |
| unknown kind | `unsupported` node, no reads or writes | `seq` |
