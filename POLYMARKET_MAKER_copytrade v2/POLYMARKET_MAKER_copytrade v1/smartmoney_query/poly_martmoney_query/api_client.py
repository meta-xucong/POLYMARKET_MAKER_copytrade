from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterable, List, Tuple

import requests

from .models import Position, Trade


def _parse_datetime(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value
    try:
        if isinstance(value, (int, float)):
            if value > 1e12:
                value = value / 1000.0
            return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = dt.datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=dt.timezone.utc)
            return parsed
    except Exception:
        return None
    return None


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pick_first(raw: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


class DataApiClient:
    def __init__(
        self,
        host: str = "https://data-api.polymarket.com",
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_positions(
        self,
        user: str,
        *,
        size_threshold: float = 0.0,
        page_size: int = 500,
        max_pages: int = 20,
        return_info: bool = False,
    ) -> List[Position] | Tuple[List[Position], Dict[str, Any]]:
        url = f"{self.host}/positions"
        page_size = max(1, min(int(page_size), 500))
        max_pages = max(1, int(max_pages))
        offset = 0
        pages = 0
        ok = True
        incomplete = False
        last_error = None
        positions: List[Position] = []
        total = None

        while pages < max_pages:
            params = {
                "user": user,
                "limit": page_size,
                "offset": offset,
                "sizeThreshold": size_threshold,
            }
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                ok = False
                incomplete = True
                last_error = str(exc)
                break

            items: List[Dict[str, Any]] = []
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                items = payload.get("data") or payload.get("positions") or payload.get("results") or []
                if total is None:
                    total = payload.get("count") or payload.get("total")
            if not isinstance(items, list):
                ok = False
                incomplete = True
                last_error = "invalid_payload"
                break

            for raw in items:
                if not isinstance(raw, dict):
                    continue
                pos = self._to_position(raw)
                if pos is not None:
                    positions.append(pos)

            pages += 1
            offset += page_size
            if len(items) < page_size:
                break

        info: Dict[str, Any] = {
            "ok": ok,
            "incomplete": incomplete,
            "last_error": last_error,
            "pages": pages,
            "total": int(total) if total is not None else len(positions),
        }
        if return_info:
            return positions, info
        return positions

    def fetch_trades(
        self,
        user: str,
        *,
        start_time: dt.datetime,
        page_size: int = 500,
        max_pages: int = 20,
        taker_only: bool = False,
    ) -> List[Trade]:
        start_ts = int(start_time.timestamp())
        end_ts = int(dt.datetime.now(tz=dt.timezone.utc).timestamp())
        url = f"{self.host}/activity"
        page_size = max(1, min(int(page_size), 500))
        max_pages = max(1, int(max_pages))
        offset = 0
        pages = 0
        trades: List[Trade] = []

        while pages < max_pages:
            params = {
                "user": user,
                "type": "TRADE",
                "limit": page_size,
                "offset": offset,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
                "start": start_ts,
                "end": end_ts,
            }
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                payload = resp.json()
            except Exception:
                break

            items: List[Dict[str, Any]] = []
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                items = payload.get("data") or payload.get("activity") or payload.get("results") or []
            if not isinstance(items, list) or not items:
                break

            for raw in items:
                if not isinstance(raw, dict):
                    continue
                if taker_only and str(raw.get("role") or "").lower() not in ("taker", ""):
                    continue
                trade = self._to_trade(raw)
                if trade is not None:
                    trades.append(trade)

            pages += 1
            offset += page_size
            if len(items) < page_size:
                break

        return trades

    def fetch_activity_actions(
        self,
        user: str,
        *,
        start_time: dt.datetime,
        end_time: dt.datetime,
        page_size: int = 500,
        max_offset: int = 10000,
        return_info: bool = False,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        url = f"{self.host}/activity"
        page_size = max(1, min(int(page_size), 500))
        max_offset = max(0, min(int(max_offset), 10000))
        offset = 0
        ok = True
        incomplete = False
        last_error = None
        results: List[Dict[str, Any]] = []

        while offset <= max_offset:
            params = {
                "user": user,
                "type": "TRADE",
                "limit": page_size,
                "offset": offset,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
                "start": int(start_time.timestamp()),
                "end": int(end_time.timestamp()),
            }
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                ok = False
                incomplete = True
                last_error = str(exc)
                break

            items: List[Dict[str, Any]] = []
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                items = payload.get("data") or payload.get("activity") or payload.get("results") or []
            if not isinstance(items, list):
                ok = False
                incomplete = True
                last_error = "invalid_payload"
                break
            if not items:
                break

            results.extend([item for item in items if isinstance(item, dict)])
            if len(items) < page_size:
                break
            offset += page_size

        info = {
            "ok": ok,
            "incomplete": incomplete,
            "last_error": last_error,
            "limit": page_size,
            "total": len(results),
            "max_offset_reached": offset >= max_offset,
        }
        if return_info:
            return results, info
        return results, info

    def _to_position(self, raw: Dict[str, Any]) -> Position | None:
        condition_id = _pick_first(raw, ("conditionId", "condition_id", "marketId", "market_id"))
        outcome_index = _pick_first(raw, ("outcomeIndex", "outcome_index"))
        size = _pick_first(raw, ("size", "shares", "positionSize"))
        avg_price = _pick_first(raw, ("avgPrice", "avg_price", "averagePrice"))
        slug = _pick_first(raw, ("slug", "marketSlug"))
        title = _pick_first(raw, ("title", "question", "marketTitle"))
        end_date_raw = _pick_first(raw, ("endDate", "end_date"))
        end_date = _parse_datetime(end_date_raw)

        if condition_id is None or outcome_index is None:
            return None
        try:
            outcome_index_val = int(outcome_index)
        except Exception:
            outcome_index_val = None

        return Position(
            condition_id=str(condition_id),
            outcome_index=outcome_index_val,
            size=_coerce_float(size),
            avg_price=_coerce_float(avg_price),
            slug=str(slug) if slug is not None else None,
            title=str(title) if title is not None else None,
            end_date=end_date,
            raw=raw,
        )

    def _to_trade(self, raw: Dict[str, Any]) -> Trade | None:
        side = _pick_first(raw, ("side", "takerSide", "tradeSide"))
        size = _pick_first(raw, ("size", "qty", "amount", "shares"))
        price = _pick_first(raw, ("price", "fillPrice", "avgPrice"))
        timestamp_raw = _pick_first(raw, ("timestamp", "time", "createdAt"))
        timestamp = _parse_datetime(timestamp_raw)
        if timestamp is None:
            return None
        market_id = _pick_first(raw, ("marketId", "market_id", "conditionId", "condition_id"))

        return Trade(
            side=str(side) if side is not None else None,
            size=_coerce_float(size),
            price=_coerce_float(price),
            timestamp=timestamp,
            raw=raw,
            market_id=str(market_id) if market_id is not None else None,
        )
