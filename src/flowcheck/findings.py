from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str  # "error" (certain from the YAML) or "warning" (heuristic)
    node_id: str  # YAML path of the step, e.g. "workflow[3].for_each.steps[0].task"
    message: str
    hint: str = ""
    node_name: str = ""
    node_type: str = ""
    line: Optional[int] = None
    variable: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def key(self):
        return (self.rule_id, self.node_id, self.variable)
