import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace(get=lambda *a, **k: None)

from poly_maker_autorun import AutoRunManager, GlobalConfig


def _build_manager(cfg: GlobalConfig) -> AutoRunManager:
    return AutoRunManager(cfg, strategy_defaults={}, run_params_template={})


def test_default_mode_is_classic():
    cfg = GlobalConfig.from_dict({})
    assert cfg.strategy_mode == "classic"
    manager = _build_manager(cfg)
    assert manager._is_aggressive_mode() is False
    assert manager._burst_slots() == 0


def test_aggressive_mode_uses_burst_slots_and_queue_promotion():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "strategy_mode": "aggressive",
                "aggressive_burst_slots": 3,
            }
        }
    )
    manager = _build_manager(cfg)
    assert manager._is_aggressive_mode() is True
    assert manager._burst_slots() == 3

    manager._enqueue_pending_topic("t1")
    manager._enqueue_burst_topic("t1", promote=True)
    assert "t1" not in manager.pending_topics
    assert manager.pending_burst_topics[0] == "t1"


def test_classic_mode_drains_burst_queue_into_pending():
    cfg = GlobalConfig.from_dict({"scheduler": {"strategy_mode": "classic"}})
    manager = _build_manager(cfg)
    manager.pending_burst_topics = ["x", "y"]
    manager.pending_topics = ["z"]

    manager._normalize_pending_queues_for_mode()

    assert manager.pending_burst_topics == []
    assert manager.pending_topics == ["z", "x", "y"]


def test_schedule_pending_topics_pauses_and_defers_when_low_balance():
    cfg = GlobalConfig.from_dict({"scheduler": {"buy_pause_min_free_balance": 20}})
    manager = _build_manager(cfg)
    manager.pending_topics = ["t1"]
    manager.pending_burst_topics = ["t2"]

    captured = []
    manager._is_buy_paused_by_balance = lambda: True  # type: ignore[assignment]
    manager._append_exit_token_record = lambda token_id, reason, **kwargs: captured.append((token_id, reason, kwargs))  # type: ignore[assignment]

    manager._schedule_pending_topics()

    assert manager.pending_topics == []
    assert manager.pending_burst_topics == []
    assert manager._buy_pause_deferred_tokens == {"t1", "t2"}
    assert len(captured) == 2
    assert all(reason == "LOW_BALANCE_PAUSE" for _, reason, _ in captured)


def test_refresh_topics_defers_new_topics_when_low_balance_pause_active():
    cfg = GlobalConfig.from_dict({"scheduler": {"buy_pause_min_free_balance": 20}})
    manager = _build_manager(cfg)

    manager._is_buy_paused_by_balance = lambda: True  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "a", "token_id": "a"},
        {"topic_id": "b", "token_id": "b"},
    ]
    manager._load_copytrade_sell_signals = lambda: set()  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]

    captured = []
    manager._append_exit_token_record = lambda token_id, reason, **kwargs: captured.append((token_id, reason, kwargs))  # type: ignore[assignment]

    manager._refresh_topics()

    assert manager.pending_topics == []
    assert manager.pending_burst_topics == []
    assert manager._buy_pause_deferred_tokens == {"a", "b"}
    assert {token_id for token_id, _, _ in captured} == {"a", "b"}
    assert manager.handled_topics.issuperset({"a", "b"})


def test_low_balance_pause_refill_filter_blocks_during_pause_and_releases_after_resume():
    cfg = GlobalConfig.from_dict({"scheduler": {"buy_pause_min_free_balance": 20}})
    manager = _build_manager(cfg)
    record = {
        "token_id": "x",
        "exit_reason": "LOW_BALANCE_PAUSE",
        "exit_ts": 1.0,
        "exit_data": {"has_position": False},
        "refillable": True,
    }

    manager._buy_paused_due_to_balance = True
    blocked = manager._filter_refillable_tokens([record])
    assert blocked == []

    manager._buy_paused_due_to_balance = False
    released = manager._filter_refillable_tokens([record])
    assert len(released) == 1
    assert released[0]["token_id"] == "x"


def test_fetch_recent_trades_retries_after_timeout():
    import poly_maker_autorun as autorun

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [{"side": "SELL", "asset": "t1"}]

    calls = {"n": 0}

    class _TimeoutExc(Exception):
        pass

    old_requests = autorun.requests
    old_attempts = autorun.DATA_API_TRADE_RETRY_ATTEMPTS
    old_backoff = autorun.DATA_API_RETRY_BACKOFF_SEC
    old_sleep = autorun.time.sleep
    try:
        def _fake_get(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _TimeoutExc("timeout")
            return _Resp()

        autorun.requests = types.SimpleNamespace(
            get=_fake_get,
            Timeout=_TimeoutExc,
            RequestException=Exception,
        )
        autorun.DATA_API_TRADE_RETRY_ATTEMPTS = 2
        autorun.DATA_API_RETRY_BACKOFF_SEC = (0.0,)
        autorun.time.sleep = lambda *_args, **_kwargs: None

        rows, info = autorun._fetch_recent_trades_from_data_api("0xabc", limit=10)
        assert info == "ok"
        assert len(rows) == 1
        assert calls["n"] == 2
    finally:
        autorun.requests = old_requests
        autorun.DATA_API_TRADE_RETRY_ATTEMPTS = old_attempts
        autorun.DATA_API_RETRY_BACKOFF_SEC = old_backoff
        autorun.time.sleep = old_sleep


def test_fetch_recent_trades_returns_last_error_after_retries():
    import poly_maker_autorun as autorun

    calls = {"n": 0}

    class _ReqExc(Exception):
        pass

    old_requests = autorun.requests
    old_attempts = autorun.DATA_API_TRADE_RETRY_ATTEMPTS
    old_backoff = autorun.DATA_API_RETRY_BACKOFF_SEC
    old_sleep = autorun.time.sleep
    try:
        def _fake_get(*args, **kwargs):
            calls["n"] += 1
            raise _ReqExc("boom")

        autorun.requests = types.SimpleNamespace(
            get=_fake_get,
            Timeout=Exception,
            RequestException=_ReqExc,
        )
        autorun.DATA_API_TRADE_RETRY_ATTEMPTS = 3
        autorun.DATA_API_RETRY_BACKOFF_SEC = (0.0,)
        autorun.time.sleep = lambda *_args, **_kwargs: None

        rows, info = autorun._fetch_recent_trades_from_data_api("0xabc", limit=10)
        assert rows == []
        assert "attempt=3/3" in info
        assert calls["n"] == 3
    finally:
        autorun.requests = old_requests
        autorun.DATA_API_TRADE_RETRY_ATTEMPTS = old_attempts
        autorun.DATA_API_RETRY_BACKOFF_SEC = old_backoff
        autorun.time.sleep = old_sleep
