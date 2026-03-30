import importlib.util
import sys
import threading
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "POLYMARKET_MAKER_AUTO" / "total_liquidation_manager.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pm_total_liq_regression", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_reentry_taker_uses_fresh_book_quote_when_ws_is_stale():
    module = _load_module()
    manager = module.TotalLiquidationManager.__new__(module.TotalLiquidationManager)

    position_state = {"size": 0.0}
    manager._get_cached_client = lambda: object()
    manager._resolve_bid_ask = lambda autorun, token_id: (0.0, 0.0)
    manager._is_quote_fresh = lambda autorun, token_id, max_age_sec=30.0: False
    manager._fetch_single_position_size = lambda token_id: position_state["size"]

    def _place_buy_ioc(client, token_id, price, size):
        assert price == 0.89
        assert size == 10.0
        position_state["size"] = 10.0

    manager._place_buy_ioc = _place_buy_ioc

    class FakeAutorun:
        def __init__(self):
            self._ws_cache_lock = threading.RLock()
            self._ws_cache = {}

        @staticmethod
        def _fetch_clob_top_of_book(token_id):
            return {
                "ok": True,
                "bid": 0.88,
                "ask": 0.89,
                "source": "clob_book",
            }

    autorun = FakeAutorun()

    result = manager.reenter_single_token_taker(
        autorun,
        "tok",
        target_size=10.0,
        reference_buy_price=0.90,
    )

    assert result["ok"] is True
    assert result["quote_source"] == "clob_book"
    assert autorun._ws_cache["tok"]["best_ask"] == 0.89
