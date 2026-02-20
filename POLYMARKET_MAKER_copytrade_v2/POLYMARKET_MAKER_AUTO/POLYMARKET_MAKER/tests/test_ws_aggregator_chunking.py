import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _WebsocketStub:
    class WebSocketApp:  # pragma: no cover - 仅用于导入占位
        def __init__(self, *args, **kwargs):
            pass


sys.modules.setdefault("websocket", _WebsocketStub())

from Volatility_arbitrage_main_ws import WSAggregatorClient


class _FakeWS:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)


def test_flush_pending_subscriptions_sends_subscribe_in_chunks():
    client = WSAggregatorClient(subscribe_chunk_size=2, subscribe_chunk_interval_sec=0.0)
    ws = _FakeWS()

    with client._lock:
        client._pending_subscribe.update({"a", "b", "c", "d", "e"})

    client._flush_pending_subscriptions(ws)

    # 5 个 token，chunk_size=2 -> 至少 3 条 subscribe 消息
    subscribe_msgs = [msg for msg in ws.sent if '"operation": "subscribe"' in msg]
    assert len(subscribe_msgs) == 3


def test_flush_pending_subscriptions_sends_unsubscribe_in_chunks():
    client = WSAggregatorClient(subscribe_chunk_size=3, subscribe_chunk_interval_sec=0.0)
    ws = _FakeWS()

    with client._lock:
        client._pending_unsubscribe.update({"a", "b", "c", "d"})

    client._flush_pending_subscriptions(ws)

    unsubscribe_msgs = [msg for msg in ws.sent if '"operation": "unsubscribe"' in msg]
    assert len(unsubscribe_msgs) == 2
