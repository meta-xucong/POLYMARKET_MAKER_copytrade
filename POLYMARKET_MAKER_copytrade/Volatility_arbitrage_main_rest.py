from __future__ import annotations

from typing import Any


class NoOpClient:
    def get_orderbook(self, token_id: str) -> dict[str, Any]:
        return {"bids": [], "asks": [], "best_bid": None, "best_ask": None}

    def list_positions(self) -> list[dict[str, Any]]:
        return []

    def list_open_orders(self) -> list[dict[str, Any]]:
        return []

    def place_order(self, *args, **kwargs) -> dict[str, Any]:
        return {}

    def cancel_order(self, *args, **kwargs) -> dict[str, Any]:
        return {}


def get_client() -> NoOpClient:
    return NoOpClient()

