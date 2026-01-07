from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

COPYTRADE_ROOT = Path(__file__).resolve().parent
V2_ROOT = COPYTRADE_ROOT.parent
LEGACY_COPYTRADE_ROOT = V2_ROOT.parent / "POLYMARKET_MAKER_copytrade"

if str(LEGACY_COPYTRADE_ROOT) not in sys.path:
    sys.path.insert(0, str(LEGACY_COPYTRADE_ROOT))

from smartmoney_query.api_client import DataApiClient

from modules.signal_tracker import SignalEvent, SignalTracker, Topic, topic_to_payload


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("配置文件必须是 JSON 对象")
    return payload


def resolve_target_accounts(cfg: Dict[str, Any]) -> List[str]:
    target_addresses = cfg.get("target_addresses")
    if target_addresses is None:
        accounts_cfg = cfg.get("accounts", {}) if isinstance(cfg.get("accounts"), dict) else {}
        target_addresses = accounts_cfg.get("target_accounts", [])
    return [str(addr).strip() for addr in (target_addresses or []) if str(addr).strip()]


def infer_outcome_side(topic: Topic) -> Optional[str]:
    if topic.outcome_index is None:
        return None
    if topic.outcome_index == 0:
        return "YES"
    if topic.outcome_index == 1:
        return "NO"
    return None


def build_signal_tracker(
    cfg: Dict[str, Any],
    *,
    logger: Any = None,
) -> Tuple[DataApiClient, SignalTracker, float]:
    target_addresses = resolve_target_accounts(cfg)
    if not target_addresses:
        raise ValueError("未配置 target_addresses/target_accounts")

    signal_cfg = cfg.get("signal_tracking", {}) if isinstance(cfg.get("signal_tracking"), dict) else {}
    risk_cfg = cfg.get("risk", {}) if isinstance(cfg.get("risk"), dict) else {}

    poll_interval = float(signal_cfg.get("poll_interval_sec", 5.0))
    data_client = DataApiClient()

    tracker = SignalTracker(
        data_client,
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
        logger=logger,
    )
    return data_client, tracker, poll_interval


def events_to_topics(events: Iterable[SignalEvent]) -> List[Dict[str, Any]]:
    payloads: List[Dict[str, Any]] = []
    for event in events:
        for topic in event.topics:
            side = infer_outcome_side(topic)
            payload = topic_to_payload(topic, side=side)
            payload["signal_type"] = event.signal_type
            payload["source_account"] = event.source_account
            payloads.append(payload)
    return payloads


def write_topics_snapshot(path: Path, topics: List[Dict[str, Any]]) -> None:
    if not topics:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "topics": topics,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="copytrade signal runner")
    parser.add_argument(
        "--config",
        type=Path,
        default=COPYTRADE_ROOT / "config.json",
        help="copytrade 配置文件路径",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=COPYTRADE_ROOT / "data" / "copytrade_topics.json",
        help="输出 topics JSON 路径",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="仅轮询一次并退出",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    cfg = load_config(args.config)
    _, tracker, poll_interval = build_signal_tracker(cfg)

    while True:
        events = tracker.poll()
        topics = events_to_topics(events)
        if topics:
            write_topics_snapshot(args.output, topics)
        if args.once:
            break
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
