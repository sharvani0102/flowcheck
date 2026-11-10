import textwrap

import pytest

from flowcheck.ir import EdgeKind
from flowcheck.parser import ParseError, parse_text


def wf(body: str, extra: str = ""):
    head = "name: t\ngoal: g\n" + (extra + "\n" if extra else "") + "workflow:\n"
    return parse_text(head + textwrap.indent(textwrap.dedent(body).strip("\n") + "\n", "  "))


def by_name(w, name):
    return next(n for n in w.nodes.values() if n.name == name)


def reads(n):
    return {(r.name, r.field, r.kind) for r in n.reads}


def writes(n):
    return {w.name for w in n.writes}


def test_step_and_task_reads_all_templated_fields():
    w = wf("""
    - step:
        name: "s_{{a}}"
        instruction: "use {{b.c}} and {{ d }}"
        save_as: "out_{{e}}"
        system_prompt: "{{ignored}}"
    - task:
        name: t
        instruction: "x"
        system_prompt: "{{f}}"
        save_as: tout
    """)
    s, t = by_name(w, "s_{{a}}"), by_name(w, "t")
    assert reads(s) == {("a", "name", "template"), ("b", "instruction", "template"),
                        (" d ", "instruction", "template"), ("e", "save_as", "template")}
    assert s.sees_history is True and t.sees_history is False
    assert s.writes[0].pattern is not None  # templated save_as
    assert reads(t) == {("f", "system_prompt", "template")}
    assert writes(t) == {"tout"}


def test_variable_steps():
    w = wf("""
    - set_variable: {name: x, value: "{{y}} + 1"}
    - increment: n
    - input: {prompt: "{{q}}"}
    - input: {prompt: "p", save_as: ans}
    - return: x
    """)
    assert reads(by_name(w, "set_variable:x")) == {("y", "value", "template")}
    assert writes(by_name(w, "set_variable:x")) == {"x"}
    assert reads(by_name(w, "increment:n")) == {("n", "increment", "increment")}
    inputs = [n for n in w.nodes.values() if n.type == "input"]
    assert [writes(n) for n in inputs] == [{"user_input"}, {"ans"}]
    assert reads(by_name(w, "return:x")) == {("x", "return", "return")}


def test_if_cfg_and_condition_reads():
    w = wf("""
    - if:
        condition: "{{a}} == 1 and b > c or flag"
        then:
          - task: {name: t1, instruction: i}
    """)
    h = next(n for n in w.nodes.values() if n.type == "if")
    assert reads(h) == {("a", "condition", "template"), ("b", "condition", "cond"),
                        ("c", "condition", "cond"), ("flag", "condition", "truth")}
    kinds = {e.kind for e in w.succ_edges(h.id)}
    assert kinds == {EdgeKind.BRANCH_TRUE, EdgeKind.BRANCH_FALSE}
    join = next(n for n in w.nodes.values() if n.type == "join")
    assert {w.nodes[e.src].name for e in w.pred_edges(join.id)} == {"t1", h.name}


def test_while_and_for_each_cfg():
    w = wf("""
    - while:
        condition: "i < 3"
        steps:
          - task: {name: body, instruction: i}
    - for_each:
        variable: it
        in: items
        steps:
          - task: {name: fbody, instruction: "{{it}}"}
    """)
    wh = next(n for n in w.nodes.values() if n.type == "while")
    body = by_name(w, "body")
    assert any(e.kind == EdgeKind.LOOP_BACK and e.dst == wh.id for e in w.succ_edges(body.id))
    assert any(e.kind == EdgeKind.BRANCH_FALSE for e in w.succ_edges(wh.id))
    fe = next(n for n in w.nodes.values() if n.type == "for_each")
    assert reads(fe) == {("items", "in", "flat")}
    bind = next(n for n in w.nodes.values() if n.type == "bind")
    assert writes(bind) == {"it"}
    # for_each over a variable may run zero times: entry goes to the header.
    assert not fe.extra["must_run"]


def test_for_each_over_literal_list_runs_at_least_once():
    w = wf("""
    - for_each:
        variable: it
        in: [1, 2]
        steps:
          - task: {name: fbody, instruction: "{{it}}"}
    """)
    bind = next(n for n in w.nodes.values() if n.type == "bind")
    entry_succ = [e.dst for e in w.succ_edges(w.entry)]
    assert entry_succ == [bind.id]


def test_parallel_list_form_discards_and_module_form_exports():
    w = wf("""
    - parallel:
        - task: {name: a, instruction: i, save_as: x}
        - task: {name: b, instruction: i, save_as: y}
    - parallel:
        module: m.yaml
        parameters_list: "{{plist}}"
        save_results_as: res
        max_workers: 2
    - gather:
        calls:
          - {module: m.yaml, parameters: {p: "{{z}}"}, save_as: g1}
          - {module: m.yaml}
        save_results_as: all
    """)
    a = by_name(w, "a")
    assert w.scopes[a.scope].discards_writes
    join = next(n for n in w.nodes.values() if n.type == "join")
    assert join.extra["fork"] is not None
    pm = next(n for n in w.nodes.values() if n.type == "parallel_module")
    assert reads(pm) == {("plist", "parameters_list", "flat")}
    assert writes(pm) == {"result_N", "res"}
    g = next(n for n in w.nodes.values() if n.type == "gather")
    assert writes(g) == {"g1", "gather_result_2", "all"}
    assert reads(g) == {("z", "parameters.p", "template")}


def test_unsupported_step_and_traps():
    w = wf("""
    - discover: {name: things, from: x}
    - switch:
        on: v
        cases: {a: []}
    """)
    assert any(n.type == "unsupported" for n in w.nodes.values())
    assert len(w.traps) == 2
    assert all(not n.writes for n in w.nodes.values() if n.type == "unsupported")


def test_line_numbers_point_at_the_step():
    w = wf("""
    - task: {name: first, instruction: i}
    - task:
        name: second
        instruction: i
    """)
    assert by_name(w, "first").line == 4
    assert by_name(w, "second").line == 6


def test_env_defaults_are_applied():
    w = wf("- task: {name: t, instruction: i}", extra='parameters:\n  p: "${P:-dflt}"\n  q: "${Q}"')
    assert w.parameters == {"p": "dflt", "q": "${Q}"}


@pytest.mark.parametrize("text", ["- just a list", "name: x\ngoal: y\n", "a: [unclosed"])
def test_parse_errors(text):
    with pytest.raises(ParseError):
        parse_text(text)
