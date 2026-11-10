"""AgentSPEX YAML -> FlowCheck IR (control-flow graph with reads and writes).

Every rule here follows ``docs/agentspex-semantics.md``; the comments cite the
AgentSPEX source line each behaviour comes from (paths relative to
``AgentSPEX/src/harness/``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .ir import Edge, EdgeKind, Node, Read, Scope, Trap, Workflow, Write
from .templates import condition_reads, has_template, template_reads, template_to_pattern
from .yamlload import load_yaml

# Dispatch order of interpreter._dispatch_step (interpreter.py:280-359).
STEP_KEYS = ("step", "task", "for_each", "if", "parallel", "while", "switch",
             "increment", "set_variable", "input", "call", "gather", "return")

# Names present in the context without any step writing them (semantics §6).
TOP_BUILTINS = frozenset({
    "task_name", "goal", "task_output_dir", "sandbox_output_dir", "prev_output",
    "submodule_tools", "submodule_registry", "workflow_file", "workflow_dir",
    "workflow_root", "conversation_history",
    "problem_statement",  # only when passed on the CLI (agent.py:621-626); see §12.2
})
MODULE_BUILTINS = frozenset({
    "_call_depth", "_parent_context", "stop_on_submodule_result", "stop_on_return",
})
# Config keys copied into the context when set (agent.py:590-616).
CONFIG_KEYS = frozenset({
    "enabled_tools", "enabled_submodules", "expose_submodules_as_tools",
    "allow_inline_tools_without_enable", "inline_tool_calls_only",
    "enable_inline_tool_calls", "stop_on_return", "require_submodule_submit",
    "max_workflow_steps", "swebench_mode", "enable_git_filtering",
    "require_thought_section", "thought_missing_message", "thought_footer_message",
    "submit_reminder_message",
})


class ParseError(Exception):
    """The file cannot be turned into a workflow at all (CLI exit code 2)."""


def parse_file(path: str | Path) -> Workflow:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ParseError(f"cannot read file: {e}") from e
    return parse_text(text, str(path))


def parse_text(text: str, path: str = "<string>") -> Workflow:
    try:
        data, lines = load_yaml(text)
    except yaml.YAMLError as e:
        raise ParseError(f"YAML syntax error: {e}") from e
    return build_workflow(data, lines, path)


def build_workflow(data: Any, lines: Dict[int, int], path: str) -> Workflow:
    if not isinstance(data, dict):
        raise ParseError("top level is not a mapping")
    if not isinstance(data.get("workflow"), list):
        raise ParseError("'workflow' is missing or is not a list (yaml_parser.py:83-84)")
    b = _Builder(lines)
    wf = b.build(data, path)
    wf.raw = data
    return wf


def _text(v: Any) -> str:
    return "" if v is None else (v if isinstance(v, str) else str(v))


class _Builder:
    def __init__(self, lines: Dict[int, int]) -> None:
        self.lines = lines
        self.nodes: Dict[int, Node] = {}
        self.edges: List[Edge] = []
        self.scopes: Dict[int, Scope] = {}
        self.traps: List[Trap] = []

    # -- graph helpers -------------------------------------------------
    def node(self, type_: str, name: str, scope: int, path: str,
             line: Optional[int], **kw: Any) -> Node:
        n = Node(id=len(self.nodes), type=type_, name=name, scope=scope,
                 yaml_path=path, line=line, **kw)
        self.nodes[n.id] = n
        return n

    def scope(self, kind: str, parent: Optional[int], discards: bool = False,
              fork: Optional[int] = None, label: str = "",
              header: Optional[int] = None, arm: str = "") -> int:
        s = Scope(id=len(self.scopes), kind=kind, parent=parent,
                  discards_writes=discards, fork=fork, label=label, header=header, arm=arm)
        self.scopes[s.id] = s
        return s.id

    Pending = List[Tuple[int, EdgeKind, Optional[str]]]

    def connect(self, pending: "Pending", dst: int,
                force: Optional[EdgeKind] = None) -> None:
        for src, kind, label in pending:
            self.edges.append(Edge(src, dst, force or kind, label))

    def trap(self, node: Node, message: str, hint: str = "") -> None:
        self.traps.append(Trap(node.id, message, hint))

    def line_of(self, obj: Any, fallback: Optional[int] = None) -> Optional[int]:
        return self.lines.get(id(obj), fallback) if isinstance(obj, dict) else fallback

    # -- top level -----------------------------------------------------
    def build(self, data: Dict[str, Any], path: str) -> Workflow:
        root = self.scope("workflow", None)
        entry = self.node("entry", "<entry>", root, "", self.line_of(data, 1))
        # Workflow system_prompt is expanded when history is created at start
        # (agent.py:619, yaml_prompts.py:29-31): check its reads at entry.
        entry.reads += template_reads(data.get("system_prompt"), "system_prompt",
                                      entry.line)
        for req in ("name", "goal"):
            if req not in data:
                self.trap(entry, f"the workflow has no '{req}', so AgentSPEX refuses "
                                 "to load it (yaml_parser.py:78-81)",
                          f"add a top-level '{req}:' field")
        pending = self.seq(data["workflow"], [(entry.id, EdgeKind.SEQ, None)],
                           root, "workflow")
        exit_ = self.node("exit", "<exit>", root, "", None)
        self.connect(pending, exit_.id)

        params = data.get("parameters")
        params = params if isinstance(params, dict) else {}
        builtins = set(TOP_BUILTINS | MODULE_BUILTINS | CONFIG_KEYS)
        if data.get("system_prompt"):
            builtins.add("system_prompt")
        name = _text(data.get("name")) or Path(path).stem
        return Workflow(name=name, path=path,
                        parameters={str(k): v for k, v in params.items()},
                        builtins=builtins, nodes=self.nodes, edges=self.edges,
                        entry=entry.id, exit=exit_.id, scopes=self.scopes,
                        traps=self.traps)

    def seq(self, steps: Any, pending: "Pending", scope: int, path: str) -> "Pending":
        if steps is None:
            return pending
        if not isinstance(steps, list):
            # AgentSPEX iterates whatever it gets; a mapping iterates its keys,
            # which are not steps (interpreter.py:162-187).
            n = self.node("unsupported", "<not a list>", scope, path, None)
            self.connect(pending, n.id)
            self.trap(n, f"'{path}' should be a list of steps but is a "
                         f"{type(steps).__name__}; AgentSPEX will not run these steps",
                      "write each step as a list item starting with '- '")
            return [(n.id, EdgeKind.SEQ, None)]
        for i, raw in enumerate(steps):
            pending = self.step(raw, pending, scope, f"{path}[{i}]")
        return pending

    # -- one step ------------------------------------------------------
    def step(self, raw: Any, pending: "Pending", scope: int, path: str) -> "Pending":
        line = self.line_of(raw)
        if not isinstance(raw, dict) or not raw:
            n = self.node("unsupported", "<invalid step>", scope, path, line)
            self.connect(pending, n.id)
            self.trap(n, "this list item is not a step mapping, so AgentSPEX skips it "
                         "(interpreter.py:162-187)")
            return [(n.id, EdgeKind.SEQ, None)]
        kind = next((k for k in STEP_KEYS if k in raw), None)
        if kind is None:
            key = next(iter(raw))
            n = self.node("unsupported", f"{key}:{_text((raw[key] or {}).get('name')) if isinstance(raw[key], dict) else ''}".rstrip(":"),
                          scope, path, line, extra={"kind": str(key)})
            self.connect(pending, n.id)
            self.trap(n, f"step type '{key}' is not supported by the current AgentSPEX "
                         "interpreter; it is silently skipped and writes nothing "
                         "(interpreter.py:191-206, 360-362)",
                      "rewrite it as a supported step (e.g. 'task' with save_as)")
            return [(n.id, EdgeKind.SEQ, None)]
        body = raw[kind]
        bline = self.line_of(body, line)
        handler = getattr(self, f"_{kind}")
        return handler(body, pending, scope, f"{path}.{kind}", bline)

    def _simple(self, type_: str, name: str, pending: "Pending", scope: int,
                path: str, line: Optional[int], **kw: Any) -> Tuple[Node, "Pending"]:
        n = self.node(type_, name, scope, path, line, **kw)
        self.connect(pending, n.id)
        return n, [(n.id, EdgeKind.SEQ, None)]

    def _save_as(self, n: Node, value: Any, field: str = "save_as",
                 templated: bool = True, kind: str = "save_as") -> None:
        if value is None or value == "":
            return
        text = _text(value)
        if templated and has_template(text):
            n.reads += template_reads(text, field, n.line)
            n.writes.append(Write(text, kind, pattern=template_to_pattern(text)))
        else:
            n.writes.append(Write(text, kind))

    # step / task ------------------------------------------------------
    def _llm(self, kind: str, body: Any, pending: "Pending", scope: int,
             path: str, line: Optional[int]) -> "Pending":
        if not isinstance(body, dict):
            n, out = self._simple(kind, kind, pending, scope, path, line,
                                  sees_history=(kind == "step"))
            self.trap(n, f"'{kind}:' must be a mapping with an 'instruction'; AgentSPEX "
                         f"raises when it runs this step (basic.py:{64 if kind == 'step' else 140})")
            return out
        name = _text(body.get("name")) or kind
        instr = body.get("instruction")
        n, out = self._simple(kind, name, pending, scope, path, line,
                              sees_history=(kind == "step"), instruction=_text(instr),
                              extra={"yaml_name": body.get("name")})
        n.reads += template_reads(body.get("name"), "name", line)
        n.reads += template_reads(instr, "instruction", line)
        if kind == "task":  # per-task system_prompt is honoured (basic.py:146)
            n.reads += template_reads(body.get("system_prompt"), "system_prompt", line)
        self._save_as(n, body.get("save_as"))
        if instr is None:
            self.trap(n, f"this {kind} has no 'instruction', so AgentSPEX raises KeyError "
                         f"when it runs (basic.py:{67 if kind == 'step' else 144}) and "
                         "nothing is saved", "add an 'instruction:' field")
        return out

    def _step(self, *a: Any) -> "Pending":
        return self._llm("step", *a)

    def _task(self, *a: Any) -> "Pending":
        return self._llm("task", *a)

    # variables --------------------------------------------------------
    def _set_variable(self, body: Any, pending: "Pending", scope: int, path: str,
                      line: Optional[int]) -> "Pending":
        if not isinstance(body, dict) or "name" not in body:
            n, out = self._simple("set_variable", "set_variable", pending, scope, path, line)
            self.trap(n, "set_variable needs a 'name' (variables.py:88 raises KeyError)")
            return out
        var = _text(body["name"])
        n, out = self._simple("set_variable", f"set_variable:{var}", pending, scope,
                              path, line)
        if "value" in body:
            n.reads += template_reads(_text(body["value"]), "value", line)
        # no value -> uses prev_output (variables.py:89), a built-in
        n.writes.append(Write(var, "set_variable"))
        if has_template(var):
            self.trap(n, f"set_variable 'name' is not template-expanded (variables.py:88), "
                         f"so this writes a variable literally named '{var}'",
                      "use a fixed name, or save_as on a step (which is templated)")
        return out

    def _increment(self, body: Any, pending: "Pending", scope: int, path: str,
                   line: Optional[int]) -> "Pending":
        if not isinstance(body, str):
            n, out = self._simple("increment", "increment", pending, scope, path, line)
            self.trap(n, "increment expects a bare variable name; anything else is "
                         "silently ignored (variables.py:54-56)",
                      "write e.g. '- increment: counter'")
            return out
        n, out = self._simple("increment", f"increment:{body}", pending, scope, path, line)
        n.reads.append(Read(body, body, "increment", kind="increment", line=line))
        n.writes.append(Write(body, "increment"))
        return out

    def _input(self, body: Any, pending: "Pending", scope: int, path: str,
               line: Optional[int]) -> "Pending":
        body = body if isinstance(body, dict) else {}
        n, out = self._simple("input", "input", pending, scope, path, line)
        n.reads += template_reads(body.get("prompt"), "prompt", line)
        target = _text(body.get("save_as", "user_input"))  # variables.py:215
        self._save_as(n, target, templated=False, kind="input")
        if has_template(target):
            self.trap(n, f"input 'save_as' is not template-expanded (variables.py:215), "
                         f"so this writes a variable literally named '{target}'")
        return out

    def _return(self, body: Any, pending: "Pending", scope: int, path: str,
                line: Optional[int]) -> "Pending":
        value = "prev_output" if body in (None, "") else body  # variables.py:148-152
        text = _text(value)
        n, out = self._simple("return", f"return:{text[:40]}", pending, scope, path, line)
        if has_template(text):
            n.reads += template_reads(text, "return", line)
        elif isinstance(value, str) and value.strip() and " " not in value.strip():
            name = value.strip()
            n.reads.append(Read(name, name, "return", kind="return", line=line))
        return out

    # control flow -----------------------------------------------------
    def _if(self, body: Any, pending: "Pending", scope: int, path: str,
            line: Optional[int]) -> "Pending":
        body = body if isinstance(body, dict) else {}
        cond = body.get("condition")
        h = self.node("if", f"if {_text(cond)}"[:60], scope, path, line,
                      extra={"condition": _text(cond)})
        self.connect(pending, h.id)
        reads, problem = condition_reads(cond, "condition", line)
        h.reads += reads
        if "condition" not in body or "then" not in body:
            missing = "condition" if "condition" not in body else "then"
            self.trap(h, f"'if' has no '{missing}', so AgentSPEX raises KeyError "
                         "(control.py:246-247)")
        elif problem:
            self.trap(h, problem + "; the 'then' branch never runs",
                      "rewrite it as a supported comparison, or compute a flag with "
                      "a step and test that")
        then_scope = self.scope("branch", scope, label="then", header=h.id, arm="then")
        else_scope = self.scope("branch", scope, label="else", header=h.id, arm="else")
        h.extra["arm_scopes"] = {"then": then_scope, "else": else_scope}
        then_out = self.seq(body.get("then"), [(h.id, EdgeKind.BRANCH_TRUE, "then")],
                            then_scope, f"{path}.then")
        else_out = self.seq(body.get("else") or [],
                            [(h.id, EdgeKind.BRANCH_FALSE, "else")],
                            else_scope, f"{path}.else")
        j = self.node("join", "<join>", scope, path, None, extra={"header": h.id})
        self.connect(then_out + else_out, j.id)
        return [(j.id, EdgeKind.SEQ, None)]

    def _switch(self, body: Any, pending: "Pending", scope: int, path: str,
                line: Optional[int]) -> "Pending":
        body = body if isinstance(body, dict) else {}
        var = body.get("variable")
        h = self.node("switch", f"switch {_text(var)}", scope, path, line,
                      extra={"variable": var})
        self.connect(pending, h.id)
        if isinstance(var, str) and var:
            h.reads.append(Read(var, var, "variable", kind="flat", line=line))
        else:
            bool_keys = [k for k in body if isinstance(k, bool)]
            why = ""
            if bool_keys:
                why = (" YAML 1.1 read one of its keys as the boolean "
                       f"{bool_keys[0]!r} (this happens to 'on:', 'yes:', 'no:', 'off:')")
            self.trap(h, "this switch has no string 'variable' key, so AgentSPEX looks up "
                         "None, no case ever matches, and it always runs 'default' "
                         f"(control.py:405-417).{why}",
                      "use 'variable: <name>' (quote YAML keys like 'on')")
        cases = body.get("cases") or {}
        branches: "Pending" = []
        h.extra["arm_scopes"] = {}
        if isinstance(cases, dict):
            for key, steps in cases.items():
                if not isinstance(key, str):
                    self.trap(h, f"switch case key {key!r} is a {type(key).__name__}, not a "
                                 "string; AgentSPEX compares it to the variable's value with "
                                 "exact dict lookup (control.py:415), so it can only match a "
                                 "value of that same type",
                              f"quote the key: '\"{key}\":'")
                s = self.scope("branch", scope, label=f"case {key}", header=h.id, arm=str(key))
                h.extra["arm_scopes"][str(key)] = s
                branches += self.seq(steps, [(h.id, EdgeKind.BRANCH_TRUE, str(key))],
                                     s, f"{path}.cases.{key}")
        d = self.scope("branch", scope, label="default", header=h.id, arm="default")
        h.extra["arm_scopes"]["default"] = d
        branches += self.seq(body.get("default") or [],
                             [(h.id, EdgeKind.BRANCH_FALSE, "default")], d,
                             f"{path}.default")
        j = self.node("join", "<join>", scope, path, None, extra={"header": h.id})
        self.connect(branches, j.id)
        return [(j.id, EdgeKind.SEQ, None)]

    def _while(self, body: Any, pending: "Pending", scope: int, path: str,
               line: Optional[int]) -> "Pending":
        body = body if isinstance(body, dict) else {}
        cond = body.get("condition", "")
        h = self.node("while", f"while {_text(cond)}"[:60], scope, path, line,
                      extra={"condition": _text(cond)})
        self.connect(pending, h.id)
        reads, problem = condition_reads(cond, "condition", line)
        h.reads += reads
        if problem:
            self.trap(h, problem + "; the loop body never runs",
                      "rewrite it as a supported comparison")
        s = self.scope("loop_body", scope, label=h.name, header=h.id, arm="body")
        h.extra["body_scope"] = s
        body_out = self.seq(body.get("steps") or [],
                            [(h.id, EdgeKind.BRANCH_TRUE, "enter")], s, f"{path}.steps")
        self.connect(body_out, h.id, force=EdgeKind.LOOP_BACK)
        x = self.node("loop_exit", "<loop exit>", scope, path, None, extra={"header": h.id})
        self.edges.append(Edge(h.id, x.id, EdgeKind.BRANCH_FALSE, "exit"))
        return [(x.id, EdgeKind.SEQ, None)]

    def _for_each(self, body: Any, pending: "Pending", scope: int, path: str,
                  line: Optional[int]) -> "Pending":
        body = body if isinstance(body, dict) else {}
        var = _text(body.get("variable"))
        src = body.get("in")
        h = self.node("for_each", f"for_each {var} in {_text(src)}"[:60], scope, path,
                      line, extra={"variable": var, "in": src})
        missing = [k for k in ("variable", "in", "steps") if k not in body]
        if missing:
            self.trap(h, f"for_each has no '{missing[0]}', so AgentSPEX raises KeyError "
                         "(control.py:158-160)")
        if isinstance(src, str):
            h.reads.append(Read(src, src, "in", kind="flat", line=line))
        # A non-empty YAML list literal runs at least once (control.py:170-171):
        # model it do-while style so the body is entered unconditionally.
        must_run = isinstance(src, list) and len(src) > 0
        s = self.scope("loop_body", scope, label=h.name, header=h.id, arm="body")
        h.extra["body_scope"] = s
        bind = self.node("bind", f"<{var}>", s, path, line)
        if var:
            bind.writes.append(Write(var, "loop_var"))
        if must_run:
            self.connect(pending, bind.id)
        else:
            self.connect(pending, h.id)
        self.edges.append(Edge(h.id, bind.id, EdgeKind.BRANCH_TRUE, "next item"))
        body_out = self.seq(body.get("steps") or [], [(bind.id, EdgeKind.SEQ, None)],
                            s, f"{path}.steps")
        self.connect(body_out, h.id, force=EdgeKind.LOOP_BACK)
        x = self.node("loop_exit", "<loop exit>", scope, path, None, extra={"header": h.id})
        self.edges.append(Edge(h.id, x.id, EdgeKind.BRANCH_FALSE, "exhausted"))
        h.extra["must_run"] = must_run
        return [(x.id, EdgeKind.SEQ, None)]

    # parallel / modules ---------------------------------------------------
    def _parallel(self, body: Any, pending: "Pending", scope: int, path: str,
                  line: Optional[int]) -> "Pending":
        if isinstance(body, dict) and "module" in body and "parameters_list" in body:
            return self._parallel_module(body, pending, scope, path, line)
        if not isinstance(body, list):
            n, out = self._simple("unsupported", "parallel", pending, scope, path, line)
            self.trap(n, "'parallel' must be a list of steps, or a mapping with both "
                         "'module' and 'parameters_list'; otherwise AgentSPEX iterates the "
                         "mapping's keys and runs nothing (calls.py:683-745)")
            return out
        f = self.node("fork", "parallel", scope, path, line)
        self.connect(pending, f.id)
        outs: "Pending" = []
        for i, child in enumerate(body):
            s = self.scope("parallel_branch", scope, discards=True, fork=f.id,
                           label=f"parallel[{i}]", header=f.id, arm=str(i))
            outs += self.step(child, [(f.id, EdgeKind.PARALLEL_FORK, str(i))], s,
                              f"{path}[{i}]")
        j = self.node("join", "<parallel join>", scope, path, None,
                      extra={"fork": f.id, "header": f.id})
        if not body:
            outs = [(f.id, EdgeKind.SEQ, None)]
        self.connect(outs, j.id, force=EdgeKind.JOIN)
        return [(j.id, EdgeKind.SEQ, None)]

    def _parallel_module(self, body: Dict[str, Any], pending: "Pending", scope: int,
                         path: str, line: Optional[int]) -> "Pending":
        module = _text(body.get("module"))
        n, out = self._simple("parallel_module", f"parallel {module}", pending, scope,
                              path, line, extra={"modules": [module]})
        n.reads += template_reads(module, "module", line)
        plist = body.get("parameters_list")
        if isinstance(plist, str):
            name = plist.strip().strip("{}").strip()  # calls.py:705-710
            if name:
                n.reads.append(Read(name, name, "parameters_list", kind="flat", line=line))
        n.writes.append(Write("result_N", "gather", pattern=r"^result_[0-9]+$"))
        self._save_as(n, body.get("save_results_as"), "save_results_as")
        if body.get("max_workers") is None:
            self.trap(n, "module-form parallel without 'max_workers' crashes: AgentSPEX "
                         "evaluates int(None) (calls.py:695-700)",
                      "add e.g. 'max_workers: 4'")
        return out

    def _call(self, body: Any, pending: "Pending", scope: int, path: str,
              line: Optional[int]) -> "Pending":
        body = body if isinstance(body, dict) else {}
        module = _text(body.get("module"))
        n, out = self._simple("call", f"call {module}", pending, scope, path, line,
                              extra={"modules": [module] if module else []})
        self._call_reads(n, body, line)
        self._save_as(n, body.get("save_as"))
        if "module" not in body:
            self.trap(n, "call has no 'module', so AgentSPEX raises KeyError (calls.py:113)")
        return out

    def _call_reads(self, n: Node, call: Dict[str, Any], line: Optional[int]) -> None:
        n.reads += template_reads(call.get("module"), "module", line)
        params = call.get("parameters") or {}
        if isinstance(params, dict):
            for k, v in params.items():
                n.reads += template_reads(_text(v), f"parameters.{k}", line)

    def _gather(self, body: Any, pending: "Pending", scope: int, path: str,
                line: Optional[int]) -> "Pending":
        body = body if isinstance(body, dict) else {}
        calls = body.get("calls") or []
        n, out = self._simple("gather", "gather", pending, scope, path, line,
                              extra={"modules": []})
        for i, call in enumerate(calls if isinstance(calls, list) else []):
            if not isinstance(call, dict):
                continue
            if call.get("module"):
                n.extra["modules"].append(_text(call["module"]))
            self._call_reads(n, call, line)
            if call.get("save_as"):
                self._save_as(n, call["save_as"], f"calls[{i}].save_as", kind="gather")
            else:
                n.writes.append(Write(f"gather_result_{i + 1}", "gather"))  # calls.py:523
        self._save_as(n, body.get("save_results_as"), "save_results_as", kind="gather")
        return out
