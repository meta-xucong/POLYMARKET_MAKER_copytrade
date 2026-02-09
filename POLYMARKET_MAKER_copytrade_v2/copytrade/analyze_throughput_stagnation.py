from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _hours_since(ts: Optional[datetime]) -> Optional[float]:
    if ts is None:
        return None
    return max((datetime.now(timezone.utc) - ts).total_seconds() / 3600.0, 0.0)


def _extract_tokens(payload: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def analyze(copytrade_tokens: Path, sell_signals: Path, stale_hours: float) -> Dict[str, Any]:
    token_payload = _load_json(copytrade_tokens)
    sell_payload = _load_json(sell_signals)

    tracked_tokens = _extract_tokens(token_payload, "tokens")
    sell_tokens = _extract_tokens(sell_payload, "sell_tokens")

    tracked_count = len(tracked_tokens)
    sell_count = len(sell_tokens)

    stale_tokens = 0
    recent_tokens = 0
    max_idle_hours = 0.0
    for token in tracked_tokens:
        idle = _hours_since(_parse_ts(token.get("last_seen")))
        if idle is None:
            stale_tokens += 1
            continue
        max_idle_hours = max(max_idle_hours, idle)
        if idle >= stale_hours:
            stale_tokens += 1
        else:
            recent_tokens += 1

    stale_ratio = (stale_tokens / tracked_count) if tracked_count else 0.0

    return {
        "tracked_count": tracked_count,
        "sell_count": sell_count,
        "stale_tokens": stale_tokens,
        "recent_tokens": recent_tokens,
        "stale_ratio": stale_ratio,
        "max_idle_hours": max_idle_hours,
    }


def format_report(metrics: Dict[str, Any], stale_hours: float) -> str:
    tracked_count = metrics["tracked_count"]
    stale_ratio = metrics["stale_ratio"]

    lines = [
        "=== Copytrade 成交衰减诊断 ===",
        f"跟踪 token 数: {metrics['tracked_count']}",
        f"待卖 token 数: {metrics['sell_count']}",
        f"超过 {stale_hours:.1f} 小时未更新的 token: {metrics['stale_tokens']}",
        f"近期活跃 token 数: {metrics['recent_tokens']}",
        f"陈旧占比(stale_ratio): {stale_ratio:.2%}",
        f"最大空转时长: {metrics['max_idle_hours']:.1f} 小时",
        "",
        "建议:",
    ]

    if tracked_count == 0:
        lines.append("- 当前没有跟踪 token，先检查 copytrade 拉取是否正常。")
    elif stale_ratio >= 0.75:
        lines.extend(
            [
                "- 已出现高库存锁死，建议提高新增 token 注入频率（扩充目标地址池/引入多钱包源）。",
                "- 建议开启库存分层：将最久未动的仓位做独立风控，不占用主策略预算。",
                "- 可增加\"机会再分发\"：定时把长时间无波动 token 标记为低优先级，给新 token 让路。",
            ]
        )
    elif stale_ratio >= 0.5:
        lines.extend(
            [
                "- 成交机会正在收缩，建议增加新 token 白名单来源并提高轮询频率。",
                "- 建议按 token 活跃度动态分配资金，避免预算长期被低活跃标的占用。",
            ]
        )
    else:
        lines.append("- 跟踪池仍有一定活跃度，可优先优化入场过滤和下单滑点参数来提升成交。")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="分析 copytrade 在长周期运行后的成交机会衰减")
    parser.add_argument(
        "--tokens",
        type=Path,
        default=Path(__file__).with_name("tokens_from_copytrade.json"),
        help="tokens_from_copytrade.json 路径",
    )
    parser.add_argument(
        "--sell-signals",
        type=Path,
        default=Path(__file__).with_name("copytrade_sell_signals.json"),
        help="copytrade_sell_signals.json 路径",
    )
    parser.add_argument(
        "--stale-hours",
        type=float,
        default=24.0,
        help="超过该小时数未更新则视为陈旧 token",
    )

    args = parser.parse_args()
    metrics = analyze(args.tokens, args.sell_signals, args.stale_hours)
    print(format_report(metrics, args.stale_hours))


if __name__ == "__main__":
    main()
