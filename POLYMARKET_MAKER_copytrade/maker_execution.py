from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional


@dataclass
class PriceSample:
    price: float


def _fetch_best_price(client, token_id: str, side: str) -> Optional[PriceSample]:
    method_candidates = (
        ("get_market_orderbook", {"market": token_id}),
        ("get_market_orderbook", {"token_id": token_id}),
        ("get_market_orderbook", {"market_id": token_id}),
        ("get_order_book", {"market": token_id}),
        ("get_order_book", {"token_id": token_id}),
        ("get_orderbook", {"market": token_id}),
        ("get_orderbook", {"token_id": token_id}),
        ("get_market", {"market": token_id}),
        ("get_market", {"token_id": token_id}),
        ("get_market_data", {"market": token_id}),
        ("get_market_data", {"token_id": token_id}),
        ("get_ticker", {"market": token_id}),
        ("get_ticker", {"token_id": token_id}),
        ("get_price", {"token_id": token_id, "side": "BUY" if side == "bid" else "SELL"}),
        ("get_price", {"market": token_id, "side": "BUY" if side == "bid" else "SELL"}),
    )

    for name, kwargs in method_candidates:
        fn = getattr(client, name, None)
        if not callable(fn):
            continue
        try:
            resp = fn(**kwargs)
        except TypeError:
            continue
        except Exception:
            continue
        payload = _normalize_payload(resp)
        sample = _extract_best_price(payload, side)
        if sample is not None:
            return sample

    orderbook = _fetch_orderbook(client, token_id)
    if not orderbook:
        return None
    if side == "bid":
        price = _best_bid(orderbook.get("bids") or [])
        if price is None:
            price = _coerce_float(orderbook.get("best_bid"))
    else:
        price = _best_ask(orderbook.get("asks") or [])
        if price is None:
            price = _coerce_float(orderbook.get("best_ask"))
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
    logger=None,
) -> dict[str, Any]:
    best_bid_sample = _fetch_best_price(client, token_id, "bid")
    best_bid = best_bid_sample.price if best_bid_sample is not None else None
    if best_bid is None:
        _log(logger, "warning", f"[order] BUY 无法获取 best_bid token_id={token_id}")
        return {"filled": 0.0, "avg_price": None}
    if order_size < min_order_size:
        _log(
            logger,
            "info",
            f"[order] BUY 低于最小下单量 token_id={token_id} size={order_size} min={min_order_size}",
        )
        return {"filled": 0.0, "avg_price": None}
    if order_size * best_bid < min_quote_amt:
        _log(
            logger,
            "info",
            (
                "[order] BUY 低于最小名义金额 token_id=%s size=%.6f price=%.6f min_quote=%.6f"
            )
            % (token_id, order_size, best_bid, min_quote_amt),
        )
        return {"filled": 0.0, "avg_price": None}
    _log(
        logger,
        "info",
        (
            "[order] BUY 提交 token_id=%s price=%.6f size=%.6f notional=%.6f"
        )
        % (token_id, best_bid, order_size, order_size * best_bid),
    )
    _place_order(client, token_id, "BUY", best_bid, order_size, logger=logger)
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
    logger=None,
) -> dict[str, Any]:
    best_ask_sample = _fetch_best_price(client, token_id, "ask")
    best_bid_sample = _fetch_best_price(client, token_id, "bid")
    best_ask = best_ask_sample.price if best_ask_sample is not None else None
    best_bid = best_bid_sample.price if best_bid_sample is not None else None
    price = best_bid or best_ask
    if price is None or price < floor_price:
        _log(
            logger,
            "info",
            (
                "[order] SELL 价格未达触发 token_id=%s price=%s floor=%.6f"
            )
            % (token_id, price, floor_price),
        )
        return {"filled": 0.0, "avg_price": None, "remaining": order_size}
    if order_size < min_order_size:
        _log(
            logger,
            "info",
            f"[order] SELL 低于最小下单量 token_id={token_id} size={order_size} min={min_order_size}",
        )
        return {"filled": 0.0, "avg_price": None, "remaining": order_size}
    _log(
        logger,
        "info",
        (
            "[order] SELL 提交 token_id=%s price=%.6f size=%.6f notional=%.6f"
        )
        % (token_id, price, order_size, order_size * price),
    )
    _place_order(client, token_id, "SELL", price, order_size, logger=logger)
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


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _normalize_payload(resp: Any) -> Any:
    payload = resp
    if isinstance(resp, tuple) and len(resp) == 2:
        payload = resp[1]
    if isinstance(payload, Mapping) and {"data", "status"} <= set(payload.keys()):
        payload = payload.get("data")
    return payload


def _extract_best_price(payload: Any, side: str) -> Optional[PriceSample]:
    numeric = _coerce_float(payload)
    if numeric is not None:
        return PriceSample(price=float(numeric))

    if isinstance(payload, Mapping):
        primary_keys = {
            "bid": (
                "best_bid",
                "bestBid",
                "bid",
                "highestBid",
                "bestBidPrice",
                "bidPrice",
                "buy",
            ),
            "ask": (
                "best_ask",
                "bestAsk",
                "ask",
                "offer",
                "best_offer",
                "bestOffer",
                "lowestAsk",
                "sell",
            ),
        }[side]
        for key in primary_keys:
            if key in payload:
                extracted = _extract_best_price(payload[key], side)
                if extracted is not None:
                    return extracted

        ladder_keys = {
            "bid": ("bids", "bid_levels", "buy_orders", "buyOrders"),
            "ask": ("asks", "ask_levels", "sell_orders", "sellOrders", "offers"),
        }[side]
        for key in ladder_keys:
            if key in payload:
                ladder = payload[key]
                if isinstance(ladder, Iterable) and not isinstance(ladder, (str, bytes, bytearray)):
                    for entry in ladder:
                        if isinstance(entry, Mapping) and "price" in entry:
                            candidate = _coerce_float(entry.get("price"))
                            if candidate is not None:
                                return PriceSample(price=float(candidate))
                        extracted = _extract_best_price(entry, side)
                        if extracted is not None:
                            return extracted

        for value in payload.values():
            extracted = _extract_best_price(value, side)
            if extracted is not None:
                return extracted
        return None

    if isinstance(payload, Iterable) and not isinstance(payload, (str, bytes, bytearray)):
        for item in payload:
            extracted = _extract_best_price(item, side)
            if extracted is not None:
                return extracted
        return None

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


def _place_order(
    client,
    token_id: str,
    side: str,
    price: float,
    size: float,
    *,
    logger=None,
) -> None:
    for name in ("place_order", "create_order", "submit_order", "order"):
        fn = getattr(client, name, None)
        if not callable(fn):
            continue
        try:
            fn(token_id=token_id, side=side, price=price, size=size)
            _log(logger, "info", f"[order] {side} 提交成功 token_id={token_id}")
            return
        except TypeError:
            try:
                fn(token_id, side, price, size)
                _log(logger, "info", f"[order] {side} 提交成功 token_id={token_id}")
                return
            except Exception:
                _log(
                    logger,
                    "warning",
                    f"[order] {side} 提交失败 token_id={token_id} via {name} 参数调用异常",
                )
                continue
        except Exception:
            _log(
                logger,
                "warning",
                f"[order] {side} 提交失败 token_id={token_id} via {name}",
            )
            continue
    return None


def _log(logger, level: str, message: str) -> None:
    if logger is None:
        return
    log_fn = getattr(logger, level, None)
    if callable(log_fn):
        log_fn(message)
    else:
        logger.info(message)
