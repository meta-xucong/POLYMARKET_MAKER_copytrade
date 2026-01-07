from __future__ import annotations

import datetime as dt
import time
from typing import Any, Dict, List, Optional, Tuple

from smartmoney_query.api_client import DataApiClient


_POSITIONS_CACHE: dict[tuple[str, float], tuple[float, list[dict], dict]] = {}


def _now_dt() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _to_token_key(raw: Dict[str, Any]) -> Optional[str]:
    condition_id = raw.get("condition_id") or raw.get("conditionId") or raw.get("marketId")
    outcome_index = raw.get("outcome_index") or raw.get("outcomeIndex")
    if condition_id is None or outcome_index is None:
        return None
    try:
        return f"{condition_id}:{int(outcome_index)}"
    except (TypeError, ValueError):
        return None


def _pick_first(raw: Dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


def _normalize_position(raw: Dict[str, Any]) -> Dict[str, Any]:
    token_id = _pick_first(raw, ("token_id", "tokenId", "asset", "token", "clobTokenId"))
    token_key = _pick_first(raw, ("token_key", "tokenKey")) or _to_token_key(raw)
    size = _pick_first(raw, ("size", "shares", "positionSize", "position", "amount"))
    avg_price = _pick_first(raw, ("avg_price", "avgPrice", "averagePrice"))
    condition_id = _pick_first(raw, ("condition_id", "conditionId", "marketId"))
    outcome_index = _pick_first(raw, ("outcome_index", "outcomeIndex"))
    slug = _pick_first(raw, ("slug", "marketSlug"))
    return {
        "token_id": str(token_id) if token_id is not None else None,
        "token_key": str(token_key) if token_key is not None else None,
        "size": float(size) if size is not None else 0.0,
        "avg_price": float(avg_price) if avg_price is not None else None,
        "condition_id": str(condition_id) if condition_id is not None else None,
        "outcome_index": int(outcome_index) if outcome_index is not None else None,
        "slug": str(slug) if slug is not None else None,
        "raw": raw,
    }


def fetch_positions_norm(
    client: DataApiClient,
    address: str,
    size_threshold: float = 0.0,
    *,
    refresh_sec: Optional[int] = None,
    cache_bust_mode: str = "sec",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if refresh_sec and refresh_sec > 0:
        cache_key = (address, float(size_threshold))
        cached = _POSITIONS_CACHE.get(cache_key)
        if cached:
            last_ts, positions, info = cached
            if time.time() - last_ts < refresh_sec:
                return positions, info

    positions, info = client.fetch_positions(
        address,
        size_threshold=float(size_threshold or 0.0),
        return_info=True,
    )
    normalized = []
    for pos in positions:
        raw = pos.raw if hasattr(pos, "raw") else {}
        if not isinstance(raw, dict):
            raw = {}
        raw.setdefault("condition_id", getattr(pos, "condition_id", None))
        raw.setdefault("outcome_index", getattr(pos, "outcome_index", None))
        raw.setdefault("size", getattr(pos, "size", None))
        raw.setdefault("avg_price", getattr(pos, "avg_price", None))
        raw.setdefault("slug", getattr(pos, "slug", None))
        normalized.append(_normalize_position(raw))

    if refresh_sec and refresh_sec > 0:
        cache_key = (address, float(size_threshold))
        _POSITIONS_CACHE[cache_key] = (time.time(), normalized, info)

    return normalized, info


def fetch_target_actions_since(
    client: DataApiClient,
    address: str,
    since_ms: int,
    *,
    max_offset: int = 10000,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    start_dt = dt.datetime.fromtimestamp(max(since_ms, 0) / 1000.0, tz=dt.timezone.utc)
    end_dt = _now_dt()
    raw_actions, info = client.fetch_activity_actions(
        address,
        start_time=start_dt,
        end_time=end_dt,
        max_offset=max_offset,
        return_info=True,
    )
    actions: List[Dict[str, Any]] = []
    latest_ms = since_ms
    for raw in raw_actions:
        if not isinstance(raw, dict):
            continue
        side = _pick_first(raw, ("side", "takerSide", "tradeSide"))
        token_id = _pick_first(raw, ("tokenId", "token_id", "asset", "clobTokenId"))
        condition_id = _pick_first(raw, ("conditionId", "condition_id", "marketId"))
        outcome_index = _pick_first(raw, ("outcomeIndex", "outcome_index"))
        token_key = _pick_first(raw, ("tokenKey", "token_key")) or _to_token_key(
            {
                "condition_id": condition_id,
                "outcome_index": outcome_index,
            }
        )
        price = _pick_first(raw, ("price", "fillPrice", "avgPrice"))
        timestamp_raw = _pick_first(raw, ("timestamp", "time", "createdAt"))
        timestamp_ms = None
        if isinstance(timestamp_raw, (int, float)):
            timestamp_ms = int(timestamp_raw if timestamp_raw > 1e12 else timestamp_raw * 1000)
        elif isinstance(timestamp_raw, str):
            try:
                ts_str = timestamp_raw.replace("Z", "+00:00")
                ts_dt = dt.datetime.fromisoformat(ts_str)
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=dt.timezone.utc)
                timestamp_ms = int(ts_dt.timestamp() * 1000)
            except Exception:
                timestamp_ms = None
        if timestamp_ms is not None:
            latest_ms = max(latest_ms, timestamp_ms)
        actions.append(
            {
                "side": str(side).upper() if side is not None else "",
                "token_id": str(token_id) if token_id is not None else None,
                "token_key": str(token_key) if token_key is not None else None,
                "condition_id": str(condition_id) if condition_id is not None else None,
                "outcome_index": int(outcome_index) if outcome_index is not None else None,
                "price": float(price) if price is not None else None,
                "timestamp": int(timestamp_ms / 1000) if timestamp_ms is not None else None,
                "raw": raw,
            }
        )

    info = dict(info)
    info["latest_ms"] = latest_ms
    return actions, info
