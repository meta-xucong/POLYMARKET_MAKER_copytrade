from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from smartmoney_query.api_client import DataApiClient

from modules.signal_tracker import SignalEvent, SignalTracker
from modules.topic_selector import Topic


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("配置文件必须是 JSON 对象")
    return payload


def build_signal_tracker(
    cfg: Dict[str, Any],
) -> Tuple[DataApiClient, SignalTracker, float]:
    target_addresses = cfg.get("target_addresses")
    if target_addresses is None:
        accounts_cfg = cfg.get("accounts", {}) if isinstance(cfg.get("accounts"), dict) else {}
        target_addresses = accounts_cfg.get("target_accounts", [])
    if not target_addresses:
        raise ValueError("未配置 target_addresses")

    signal_cfg = cfg.get("signal_tracking", {}) if isinstance(cfg.get("signal_tracking"), dict) else {}
    risk_cfg = cfg.get("risk", {}) if isinstance(cfg.get("risk"), dict) else {}

    poll_interval = float(signal_cfg.get("poll_interval_sec", 5))
    client = DataApiClient()
    tracker = SignalTracker(
        client,
        target_addresses,
        poll_interval_sec=poll_interval,
        position_poll_interval_sec=float(signal_cfg.get("position_poll_interval_sec", 20)),
        position_size_threshold=float(signal_cfg.get("position_size_threshold", 0.0)),
        positions_refresh_sec=signal_cfg.get("positions_refresh_sec"),
        positions_cache_bust_mode=str(signal_cfg.get("positions_cache_bust_mode", "sec")),
        sell_confirm_max=int(signal_cfg.get("sell_confirm_max", 5)),
        sell_confirm_window_sec=int(signal_cfg.get("sell_confirm_window_sec", 300)),
        sell_confirm_force_ratio=float(signal_cfg.get("sell_confirm_force_ratio", 0.5)),
        sell_confirm_force_shares=float(signal_cfg.get("sell_confirm_force_shares", 0.0)),
        blacklist_token_keys=risk_cfg.get("blacklist_token_keys") or [],
        blacklist_token_ids=risk_cfg.get("blacklist_token_ids") or [],
    )
    return client, tracker, poll_interval


def _topic_payload(
    topic: Topic,
    *,
    signal_type: str,
    timestamp: int,
    source_account: str,
) -> Dict[str, Any]:
    topic_id = topic.token_id or topic.token_key
    if not topic_id and topic.condition_id is not None and topic.outcome_index is not None:
        topic_id = f"{topic.condition_id}:{topic.outcome_index}"
    return {
        "topic_id": topic_id,
        "token_id": topic.token_id,
        "token_key": topic.token_key,
        "condition_id": topic.condition_id,
        "outcome_index": topic.outcome_index,
        "price": topic.price,
        "signal_type": signal_type,
        "timestamp": timestamp,
        "source_account": source_account,
    }


def events_to_topics(events: List[SignalEvent]) -> List[Dict[str, Any]]:
    payloads: List[Dict[str, Any]] = []
    for event in events:
        for topic in event.topics:
            payloads.append(
                _topic_payload(
                    topic,
                    signal_type=event.signal_type,
                    timestamp=event.timestamp,
                    source_account=event.source_account,
                )
            )
    return payloads


def write_topics_snapshot(path: Path, topics: List[Dict[str, Any]]) -> None:
    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(topics),
        "topics": topics,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
