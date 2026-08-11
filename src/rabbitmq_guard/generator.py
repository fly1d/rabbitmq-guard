import copy
import json
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List


JITTER_KEYS = {
    "messages",
    "messages_ready",
    "messages_unacknowledged",
    "publish_rate",
    "deliver_rate",
    "ack_rate",
    "redeliver_rate",
    "mem_used",
    "mem_limit",
    "disk_free",
    "disk_free_limit",
    "fd_used",
    "fd_total",
    "connection_open_rate",
}


def load_cases(case_dir: Path) -> List[Dict[str, Any]]:
    cases = []
    for path in sorted(case_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            cases.append(json.load(handle))
    return cases


def _jitter(value: Any, key: str, rng: random.Random) -> Any:
    if key not in JITTER_KEYS or isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    factor = rng.uniform(0.88, 1.12)
    result = value * factor
    return max(0, int(round(result))) if isinstance(value, int) else max(0.0, round(result, 3))


def _walk(value: Any, rng: random.Random, key: str = "") -> Any:
    if isinstance(value, dict):
        return {child_key: _walk(child, rng, child_key) for child_key, child in value.items()}
    if isinstance(value, list):
        return [_walk(child, rng, key) for child in value]
    return _jitter(value, key, rng)


def generate_variants(
    cases: Iterable[Dict[str, Any]], count_per_case: int, seed: int
) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    variants = []
    for case in cases:
        case_id = (case.get("scenario") or {}).get("id", "unknown")
        for index in range(count_per_case):
            variant = _walk(copy.deepcopy(case), rng)
            variant["capture"] = {
                "kind": "synthetic-variant",
                "base_case": case_id,
                "variant": index + 1,
                "seed": seed,
            }
            variants.append(variant)
    return variants


def write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count
