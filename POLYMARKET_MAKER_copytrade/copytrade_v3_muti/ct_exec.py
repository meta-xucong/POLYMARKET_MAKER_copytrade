from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple


def fetch_open_orders_norm(client) -> Tuple[List[Dict[str, Any]], bool, Optional[str]]:
    candidates = (
        ("list_open_orders", {}),
        ("get_open_orders", {}),
        ("fetch_open_orders", {}),
        ("list_orders", {"status": "OPEN"}),
        ("get_orders", {"status": "OPEN"}),
    )
    for name, kwargs in candidates:
        fn = getattr(client, name, None)
        if not callable(fn):
            continue
        try:
            resp = fn(**kwargs)
        except TypeError:
            try:
                resp = fn()
            except Exception as exc:
                return [], False, str(exc)
        except Exception as exc:
            return [], False, str(exc)
        if isinstance(resp, list):
            return [item for item in resp if isinstance(item, dict)], True, None
        if isinstance(resp, dict):
            for key in ("orders", "data", "results", "items", "list"):
                val = resp.get(key)
                if isinstance(val, list):
                    return [item for item in val if isinstance(item, dict)], True, None
    return [], False, "no_open_order_method"


def cancel_order(client, order_id: str) -> None:
    for name in ("cancel_order", "cancel", "cancel_order_by_id"):
        fn = getattr(client, name, None)
        if not callable(fn):
            continue
        try:
            fn(order_id)
            return
        except TypeError:
            try:
                fn(id=order_id)
                return
            except Exception:
                continue
        except Exception:
            continue
    raise RuntimeError("cancel_order_failed")


def get_orderbook(client, token_id: str) -> Dict[str, Any]:
    for name in ("get_orderbook", "get_order_book", "fetch_orderbook", "orderbook"):
        fn = getattr(client, name, None)
        if not callable(fn):
            continue
        try:
            resp = fn(token_id)
        except Exception:
            continue
        orderbook = _normalize_orderbook(resp)
        if orderbook is not None:
            return orderbook
    return {"bids": [], "asks": [], "best_bid": None, "best_ask": None}


def _normalize_orderbook(resp: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(resp, dict):
        return None
    bids = resp.get("bids") or resp.get("buy") or []
    asks = resp.get("asks") or resp.get("sell") or []
    if not isinstance(bids, list):
        bids = []
    if not isinstance(asks, list):
        asks = []
    best_bid = _best_price(bids)
    best_ask = _best_price(asks)
    return {"bids": bids, "asks": asks, "best_bid": best_bid, "best_ask": best_ask}


def _best_price(entries: Iterable[Any]) -> Optional[float]:
    best = None
    for entry in entries:
        if isinstance(entry, dict):
            price = entry.get("price")
        elif isinstance(entry, (list, tuple)) and entry:
            price = entry[0]
        else:
            price = None
        try:
            price_val = float(price)
        except (TypeError, ValueError):
            continue
        if best is None:
            best = price_val
        else:
            best = max(best, price_val)
    return best


def reconcile_one(
    *,
    token_id: str,
    desired_shares: float,
    my_shares: float,
    orderbook: Dict[str, Any],
    open_orders: List[Dict[str, Any]],
    now_ts: int,
    cfg: Dict[str, Any],
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    if my_shares <= 0:
        return actions
    if open_orders and cfg.get("dedupe_place", True):
        return actions
    best_bid = orderbook.get("best_bid") or orderbook.get("best_ask")
    if best_bid is None:
        return actions
    try:
        price = float(best_bid)
    except (TypeError, ValueError):
        return actions
    actions.append(
        {
            "action": "place",
            "side": "SELL",
            "token_id": token_id,
            "price": price,
            "size": float(my_shares),
            "timestamp": now_ts,
        }
    )
    return actions


def apply_actions(
    client,
    actions: List[Dict[str, Any]],
    open_orders: List[Dict[str, Any]],
    now_ts: int,
    *,
    dry_run: bool,
    cfg: Dict[str, Any],
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    if dry_run:
        return open_orders
    for action in actions:
        if action.get("action") == "cancel":
            order_id = action.get("order_id")
            if order_id:
                cancel_order(client, str(order_id))
            continue
        if action.get("action") != "place":
            continue
        side = action.get("side")
        token_id = action.get("token_id")
        price = action.get("price")
        size = action.get("size")
        if not token_id or price is None or size is None:
            continue
        _place_order(client, token_id, side, price, size)
    return open_orders


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
    raise RuntimeError("place_order_failed")

