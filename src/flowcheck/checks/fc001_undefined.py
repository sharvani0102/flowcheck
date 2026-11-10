"""FC001 undefined-read: no path to the reader writes the variable, and it is
not a parameter or built-in. Certain from the YAML, so an error (with the few
read kinds whose runtime fallback is merely suspicious reported as warnings).
"""

from __future__ import annotations

from typing import List

from ..findings import ERROR, WARNING, Finding
from ..ir import Read
from .common import Analysis, finding

RULE = "FC001"


def _describe(an: Analysis, node, r: Read):
    """(severity, message, hint) for an undefined read."""
    shown = "{{" + r.path + "}}"
    later = an.later_writers(node, r.name)
    if later:
        w = later[0]
        move = f"'{r.name}' is written later, by '{w.name}' (line {w.line}); move this step after it"
    else:
        move = None
    sugg = an.suggestion(r.name)
    did_you_mean = f"did you mean '{sugg}'?" if sugg else None
    default_hint = move or did_you_mean or (
        f"produce '{r.name}' earlier with save_as, or declare it under parameters:")

    if r.kind == "template" and r.path != r.path.strip():
        return (ERROR,
                f"placeholder {shown} has spaces inside the braces; AgentSPEX looks up the "
                f"key '{r.path}' literally and never substitutes it (templates.py:12-20)",
                "write {{" + r.path.strip() + "}}")
    if r.kind == "flat" and r.field == "in":
        if "{{" in r.path:
            why = ("for_each 'in' is not template-expanded (control.py:167-169), so the loop "
                   f"runs once over the literal string '{r.path}'")
            hint = "write the bare variable name, e.g. in: " + r.path.strip("{} ")
        elif "." in r.path:
            why = (f"for_each 'in: {r.path}' is looked up as one flat key, not a dotted path "
                   "(control.py:167-169), so the loop runs once over the literal string "
                   f"'{r.path}'")
            hint = "first save the list to its own variable (e.g. a task with save_as), then loop over that"
        else:
            why = (f"no step before this loop writes '{r.name}', so for_each runs once over "
                   f"the literal string '{r.name}' (control.py:167-169)")
            hint = default_hint
        return ERROR, why, hint
    if r.kind == "flat" and r.field == "variable":
        return (ERROR,
                f"switch variable '{r.name}' is never defined before this switch (it is looked up "
                "as one flat key, control.py:410), so it always takes 'default'",
                move or ("save the value to its own variable first" if "." in r.name
                         else default_hint))
    if r.kind == "flat":
        return (ERROR, f"'{r.field}' names '{r.name}', which is not defined here",
                default_hint)
    if r.kind == "truth":
        sev = ERROR if an.written_anywhere(r.name) else WARNING
        return (sev,
                f"condition tests '{r.path}', which is not defined here, so it is always "
                "false (conditions.py:198-206)", default_hint)
    if r.kind == "cond":
        return (ERROR,
                f"condition operand '{r.path}' is not defined here, so AgentSPEX compares "
                f"against the literal string '{r.path}' instead (conditions.py:120)",
                default_hint)
    if r.kind == "return":
        sev = ERROR if an.written_anywhere(r.name) else WARNING
        return (sev,
                f"return '{r.name}' is not defined here, so the literal string '{r.name}' is "
                "returned (variables.py:162-167)", default_hint)
    if r.field == "name":
        return (WARNING,
                f"step name uses {shown}, which is not defined here (only affects logs)",
                default_hint)
    where = "the system prompt" if r.field == "system_prompt" else f"its {r.field}"
    return (ERROR,
            f"{where} uses {shown} but no step before it writes '{r.name}' and it is not a "
            "parameter or built-in; AgentSPEX sends the placeholder to the LLM as literal "
            "text (templates.py:19-20)",
            default_hint)


def check(an: Analysis) -> List[Finding]:
    out = []
    for node in an.wf.nodes.values():
        for r in an.relevant_reads(node):
            if an.maybe_defined(node, r.name):
                continue
            if an.leak_for(node, r.name) is not None:
                continue  # reported as FC003
            sev, msg, hint = _describe(an, node, r)
            out.append(finding(RULE, sev, node, msg, hint, variable=r.name))
    return out
