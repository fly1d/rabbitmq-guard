from dataclasses import asdict, dataclass
from typing import Any, Dict, List


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    title: str
    target: str
    confidence: str
    evidence: List[str]
    explanation: str
    actions: List[str]
    sources: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
