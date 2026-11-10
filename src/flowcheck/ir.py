"""Intermediate representation: a workflow as a control-flow graph.

Every executable YAML step becomes a ``Node``. Control constructs get a
header node (``if``, ``switch``, ``while``, ``for_each``), a ``join`` node
where branches meet, and loops get a ``loop_exit`` node. List-form
``parallel`` becomes a ``fork`` and a ``join`` whose state is reset to the
fork's (child writes are discarded, AgentSPEX ``calls.py:730-745``).
See ``docs/agentspex-semantics.md`` §13 for the full mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class EdgeKind(str, Enum):
    SEQ = "seq"
    BRANCH_TRUE = "branch_true"
    BRANCH_FALSE = "branch_false"
    LOOP_BACK = "loop_back"
    PARALLEL_FORK = "parallel_fork"
    JOIN = "join"


#: Node types that call an LLM.
LLM_TYPES = frozenset({"step", "task"})
#: Header nodes of control constructs.
CONTROL_TYPES = frozenset({"if", "switch", "while", "for_each"})
#: Synthetic nodes that don't correspond to a YAML step.
SYNTHETIC_TYPES = frozenset({"entry", "exit", "join", "fork", "loop_exit", "bind"})


@dataclass
class Read:
    """A variable read.

    ``kind`` says how the runtime looks the name up:

    - ``template``: a ``{{...}}`` placeholder; unresolved means literal text.
    - ``flat``: a bare-name ``context.get`` (``for_each.in``, ``switch.variable``,
      ``parallel.parameters_list``).
    - ``cond``: a comparison operand; an undefined name becomes a string literal.
    - ``truth``: a truthiness test; an undefined name is ``False``.
    - ``return``: a ``return`` value; an undefined name is returned literally.
    - ``increment``: an ``increment`` target; an undefined value counts as 0.
    """

    name: str  # root variable name as the runtime looks it up
    path: str  # full text, e.g. "a.b.c" or " x " (whitespace kept)
    field: str  # YAML field the read came from
    kind: str = "template"
    line: Optional[int] = None


@dataclass
class Write:
    name: str  # exact name, or a display form when ``pattern`` is set
    kind: str  # save_as, set_variable, increment, input, loop_var, gather, ...
    pattern: Optional[str] = None  # regex for templated names like a_{{x}}


@dataclass
class Node:
    id: int
    type: str
    name: str
    scope: int
    yaml_path: str
    line: Optional[int] = None
    reads: List[Read] = field(default_factory=list)
    writes: List[Write] = field(default_factory=list)
    #: True for ``step`` (conversation history), False for ``task``, None otherwise.
    sees_history: Optional[bool] = None
    instruction: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.name} ({self.type})"


@dataclass
class Edge:
    src: int
    dst: int
    kind: EdgeKind
    label: Optional[str] = None


@dataclass
class Scope:
    """A region of the workflow.

    ``discards_writes`` is True only for list-form ``parallel`` children,
    whose context is a deep copy that is thrown away. Branches and loop bodies
    share the parent's context (AgentSPEX ``control.py:185-186, 276-283``).
    """

    id: int
    kind: str  # workflow | branch | loop_body | parallel_branch
    parent: Optional[int]
    discards_writes: bool = False
    fork: Optional[int] = None  # fork node id for parallel branches
    label: str = ""


@dataclass
class Trap:
    """A construct AgentSPEX silently mis-executes (reported as FC005)."""

    node: int
    message: str
    hint: str = ""


@dataclass
class Workflow:
    name: str
    path: str
    parameters: Dict[str, Any]
    builtins: Set[str]
    nodes: Dict[int, Node]
    edges: List[Edge]
    entry: int
    exit: int
    scopes: Dict[int, Scope]
    traps: List[Trap] = field(default_factory=list)
    raw: Any = None

    def __post_init__(self) -> None:
        self._succ: Dict[int, List[Edge]] = {n: [] for n in self.nodes}
        self._pred: Dict[int, List[Edge]] = {n: [] for n in self.nodes}
        for e in self.edges:
            self._succ[e.src].append(e)
            self._pred[e.dst].append(e)

    def succ_edges(self, n: int) -> List[Edge]:
        return self._succ[n]

    def pred_edges(self, n: int) -> List[Edge]:
        return self._pred[n]

    def scope_chain(self, scope_id: int) -> List[int]:
        chain = []
        cur: Optional[int] = scope_id
        while cur is not None:
            chain.append(cur)
            cur = self.scopes[cur].parent
        return chain

    def real_nodes(self) -> List[Node]:
        """Nodes that correspond to YAML steps, in id (document) order."""
        return [n for n in self.nodes.values() if n.type not in SYNTHETIC_TYPES]
