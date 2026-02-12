from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 仅用于导入目标模块，不在此文件中验证 requests 行为
requests_stub = types.SimpleNamespace(RequestException=Exception, get=lambda *_, **__: None, post=lambda *_, **__: None)
sys.modules.setdefault("requests", requests_stub)

ws_stub = types.SimpleNamespace(get_client=lambda: object(), ws_watch_by_ids=lambda *_, **__: None)
rest_stub = types.SimpleNamespace(get_client=lambda: object())
price_watch_stub = types.SimpleNamespace(resolve_token_ids=lambda *_, **__: ("YES", "NO", "", {}))

sys.modules.setdefault("Volatility_arbitrage_main_ws", ws_stub)
sys.modules.setdefault("Volatility_arbitrage_main_rest", rest_stub)
sys.modules.setdefault("Volatility_arbitrage_price_watch", price_watch_stub)

import Volatility_arbitrage_run as module


def test_resolve_wallet_prefers_client_funder():
    client = types.SimpleNamespace(funder="0xabc")
    addr, origin = module._resolve_wallet_address(client)
    assert addr == "0xabc"
    assert origin == "client.funder"


def test_resolve_wallet_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("POLY_FUNDER", "0xfeed")
    client = types.SimpleNamespace()
    addr, origin = module._resolve_wallet_address(client)
    assert addr == "0xfeed"
    assert origin == "env:POLY_FUNDER"


def test_extract_positions_official_shape_only():
    assert module._extract_positions_from_data_api_response([]) == []
    assert module._extract_positions_from_data_api_response([{"asset": "1"}]) == [{"asset": "1"}]
    assert module._extract_positions_from_data_api_response({"data": []}) is None
