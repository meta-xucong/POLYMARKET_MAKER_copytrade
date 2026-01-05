from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Dict, Set


@dataclass(frozen=True)
class Topic:
    token_id: Optional[str]
    token_key: Optional[str]
    condition_id: Optional[str]
    outcome_index: Optional[int]

    @property
    def identifier(self) -> str:
        return self.token_id or self.token_key or "unknown"


def _normalize_text(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def from_action(action: Dict[str, object]) -> Optional[Topic]:
    token_id = _normalize_text(action.get("token_id"))
    token_key = _normalize_text(action.get("token_key"))
    condition_id = _normalize_text(action.get("condition_id"))
    outcome_index_raw = action.get("outcome_index")
    outcome_index: Optional[int]
    try:
        outcome_index = int(outcome_index_raw) if outcome_index_raw is not None else None
    except (TypeError, ValueError):
        outcome_index = None

    if not token_id and not token_key:
        return None

    return Topic(
        token_id=token_id,
        token_key=token_key,
        condition_id=condition_id,
        outcome_index=outcome_index,
    )


def select_topics(
    actions: Iterable[Dict[str, object]],
    *,
    blacklist_token_keys: Optional[Iterable[str]] = None,
    blacklist_token_ids: Optional[Iterable[str]] = None,
) -> List[Topic]:
    topics: List[Topic] = []
    seen: set[str] = set()
    blacklist_keys: Set[str] = {
        str(key).strip() for key in (blacklist_token_keys or []) if str(key).strip()
    }
    blacklist_ids: Set[str] = {
        str(key).strip() for key in (blacklist_token_ids or []) if str(key).strip()
    }
    for action in actions:
        topic = from_action(action)
        if topic is None:
            continue
        if topic.token_key and topic.token_key in blacklist_keys:
            continue
        if topic.token_id and topic.token_id in blacklist_ids:
            continue
        key = topic.identifier
        if key in seen:
            continue
        seen.add(key)
        topics.append(topic)
    return topics
