from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal environments
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    class _SimpleResponse:
        def __init__(self, status_code: int, payload: bytes) -> None:
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"http_status_{self.status_code}")

        def json(self) -> object:
            return json.loads(self._payload.decode("utf-8"))

    class _SimpleRequests:
        @staticmethod
        def get(url: str, params: dict | None = None, timeout: float = 10.0) -> _SimpleResponse:
            if params:
                query = urllib.parse.urlencode(params)
                url = f"{url}?{query}"
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310
                    payload = resp.read()
                    return _SimpleResponse(resp.status, payload)
            except urllib.error.HTTPError as exc:
                return _SimpleResponse(exc.code, exc.read())

    requests = _SimpleRequests()


GAMMA_HOST = "https://gamma-api.polymarket.com"


def _request_gamma(params: Dict[str, Any]) -> list[dict]:
    try:
        resp = requests.get(f"{GAMMA_HOST}/markets", params=params, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get("markets") or payload.get("data") or payload.get("results") or []
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def gamma_fetch_markets_by_clob_token_ids(token_ids: Iterable[str]) -> Dict[str, dict]:
    ids = [str(token_id).strip() for token_id in token_ids if str(token_id).strip()]
    if not ids:
        return {}
    items = _request_gamma({"clobTokenIds": ",".join(ids)})
    result: Dict[str, dict] = {}
    for item in items:
        token_id = item.get("clobTokenId") or item.get("clob_token_id") or item.get("tokenId")
        if token_id:
            result[str(token_id)] = item
    return result


def _gamma_fetch_by_condition_id(condition_id: str) -> list[dict]:
    items = _request_gamma({"conditionIds": condition_id})
    if items:
        return items
    return _request_gamma({"conditionId": condition_id})


def resolve_token_id(
    token_key: str,
    position: Optional[Dict[str, Any]] = None,
    cache: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    if cache is None:
        cache = {}
    if token_key in cache:
        return cache[token_key]

    if position:
        for key in ("token_id", "tokenId", "asset", "token", "clobTokenId"):
            value = position.get(key)
            if value:
                cache[token_key] = str(value)
                return str(value)

    if ":" in token_key:
        condition_id, outcome_raw = token_key.split(":", 1)
        try:
            outcome_index = int(outcome_raw)
        except (TypeError, ValueError):
            outcome_index = None
        if condition_id and outcome_index is not None:
            markets = _gamma_fetch_by_condition_id(condition_id)
            for market in markets:
                outcomes = market.get("outcomes") or market.get("tokens") or []
                if isinstance(outcomes, list):
                    for outcome in outcomes:
                        if not isinstance(outcome, dict):
                            continue
                        index_val = outcome.get("outcomeIndex") or outcome.get("outcome_index")
                        try:
                            if int(index_val) == outcome_index:
                                token_id = (
                                    outcome.get("clobTokenId")
                                    or outcome.get("tokenId")
                                    or outcome.get("id")
                                )
                                if token_id:
                                    cache[token_key] = str(token_id)
                                    return str(token_id)
                        except (TypeError, ValueError):
                            continue
    return None


def market_tradeable_state(market_meta: Dict[str, Any]) -> bool:
    if not isinstance(market_meta, dict):
        return False
    closed = market_meta.get("closed") or market_meta.get("isClosed")
    if isinstance(closed, bool) and closed:
        return False
    active = market_meta.get("active") or market_meta.get("isActive")
    if isinstance(active, bool) and not active:
        return False
    status = str(market_meta.get("status") or "").lower()
    if status in {"closed", "resolved", "settled", "archived"}:
        return False
    return True
