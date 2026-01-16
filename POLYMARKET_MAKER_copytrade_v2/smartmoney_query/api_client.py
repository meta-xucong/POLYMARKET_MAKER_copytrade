from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

import requests

from .models import Trade


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


def _pick_first(raw: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


class DataApiClient:
    def __init__(
        self,
        host: str = "https://data-api.polymarket.com",
        gamma_host: str = "https://gamma-api.polymarket.com",
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.gamma_host = gamma_host.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

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

    def fetch_market_meta(self, market_id: str) -> Dict[str, Any] | None:
        if not market_id:
            return None

        endpoints = [
            (f"{self.gamma_host}/markets/{market_id}", None),
            (f"{self.gamma_host}/markets", {"conditionId": market_id, "limit": 1}),
            (f"{self.gamma_host}/markets", {"marketId": market_id, "limit": 1}),
            (f"{self.gamma_host}/markets", {"id": market_id, "limit": 1}),
        ]

        for url, params in endpoints:
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                payload = resp.json()
            except Exception:
                continue

            if isinstance(payload, dict):
                if payload.get("id") or payload.get("marketId") or payload.get("conditionId"):
                    return payload
                items = payload.get("data") or payload.get("results") or payload.get("markets")
                if isinstance(items, list) and items:
                    return items[0]
            elif isinstance(payload, list) and payload:
                return payload[0]

        return None

    def _to_trade(self, raw: Dict[str, Any]) -> Trade | None:
        side = _pick_first(raw, ["side", "takerSide", "tradeSide"])
        size = _pick_first(raw, ["size", "qty", "amount", "shares"])
        price = _pick_first(raw, ["price", "fillPrice", "avgPrice"])
        timestamp_raw = _pick_first(raw, ["timestamp", "time", "createdAt"])
        timestamp = _parse_datetime(timestamp_raw)
        if timestamp is None:
            return None
        market_id = _pick_first(raw, ["marketId", "market_id", "conditionId", "condition_id"])

        return Trade(
            side=str(side) if side is not None else None,
            size=_coerce_float(size),
            price=_coerce_float(price),
            timestamp=timestamp,
            raw=raw,
            market_id=str(market_id) if market_id is not None else None,
        )
