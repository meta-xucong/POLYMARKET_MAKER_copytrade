from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional


@dataclass
class PriceSample:
    price: float


def _fetch_best_price(client, token_id: str, side: str) -> Optional[PriceSample]:
    orderbook = _fetch_orderbook(client, token_id)
    if not orderbook:
        return None
    if side == "bid":
        price = _best_bid(orderbook.get("bids") or [])
    else:
        price = _best_ask(orderbook.get("asks") or [])
    if price is None:
        return None
    return PriceSample(price=price)


def maker_buy_follow_bid(
    client,
    token_id: str,
    order_size: float,
    *,
    poll_sec: float,
    min_quote_amt: float,
    min_order_size: float,
    stop_check,
) -> dict[str, Any]:
    best_bid = _best_bid((_fetch_orderbook(client, token_id) or {}).get("bids") or [])
    if best_bid is None:
        return {"filled": 0.0, "avg_price": None}
    if order_size < min_order_size:
        return {"filled": 0.0, "avg_price": None}
    if order_size * best_bid < min_quote_amt:
        return {"filled": 0.0, "avg_price": None}
    _place_order(client, token_id, "BUY", best_bid, order_size)
    return {"filled": float(order_size), "avg_price": float(best_bid)}


def maker_sell_follow_ask_with_floor_wait(
    client,
    token_id: str,
    order_size: float,
    floor_price: float,
    *,
    poll_sec: float,
    min_order_size: float,
    stop_check,
    sell_mode: str,
    aggressive_step: float,
    aggressive_timeout: float,
) -> dict[str, Any]:
    best_ask = _best_ask((_fetch_orderbook(client, token_id) or {}).get("asks") or [])
    best_bid = _best_bid((_fetch_orderbook(client, token_id) or {}).get("bids") or [])
    price = best_bid or best_ask
    if price is None or price < floor_price:
        return {"filled": 0.0, "avg_price": None, "remaining": order_size}
    if order_size < min_order_size:
        return {"filled": 0.0, "avg_price": None, "remaining": order_size}
    _place_order(client, token_id, "SELL", price, order_size)
    return {"filled": float(order_size), "avg_price": float(price), "remaining": 0.0}


def _fetch_orderbook(client, token_id: str) -> Optional[dict[str, Any]]:
    for name in ("get_orderbook", "get_order_book", "fetch_orderbook", "orderbook"):
        fn = getattr(client, name, None)
        if not callable(fn):
            continue
        try:
            resp = fn(token_id)
        except Exception:
            continue
        if isinstance(resp, dict):
            return resp
    return None


def _best_bid(entries: Iterable[Any]) -> Optional[float]:
    best = None
    for entry in entries:
        price = _extract_price(entry)
        if price is None:
            continue
        best = price if best is None else max(best, price)
    return best


def _best_ask(entries: Iterable[Any]) -> Optional[float]:
    best = None
    for entry in entries:
        price = _extract_price(entry)
        if price is None:
            continue
        best = price if best is None else min(best, price)
    return best


def _extract_price(entry: Any) -> Optional[float]:
    if isinstance(entry, dict):
        raw = entry.get("price")
    elif isinstance(entry, (list, tuple)) and entry:
        raw = entry[0]
    else:
        raw = None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _place_order(client, token_id: str, side: str, price: float, size: float) -> None:
    for name in ("place_order", "create_order", "submit_order", "order"):
        fn = getattr(client, name, None)
        if not callable(fn):
            continue
        try:
            fn(token_id=token_id, side=side, price=price, size=size)
            return
        except TypeError:
            try:
                fn(token_id, side, price, size)
                return
            except Exception:
                continue
        except Exception:
            continue
    return None

