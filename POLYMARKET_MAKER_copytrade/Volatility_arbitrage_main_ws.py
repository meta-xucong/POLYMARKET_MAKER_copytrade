from __future__ import annotations

from Volatility_arbitrage_main_rest import get_client as get_rest_client


def get_client() -> object:
    return get_rest_client()
