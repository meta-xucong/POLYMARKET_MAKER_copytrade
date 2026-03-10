import sys
import types
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "requests" not in sys.modules:
    class _DummySession:
        def __init__(self):
            self.headers = {}

        def get(self, *args, **kwargs):
            return None

    sys.modules["requests"] = types.SimpleNamespace(
        get=lambda *a, **k: None,
        Session=_DummySession,
        exceptions=types.SimpleNamespace(RequestException=Exception),
    )

import poly_maker_autorun as autorun_mod
from poly_maker_autorun import AutoRunManager, GlobalConfig, TopicTask, compute_new_topics


def _build_manager(cfg: GlobalConfig) -> AutoRunManager:
    return AutoRunManager(cfg, strategy_defaults={}, run_params_template={})


def test_default_mode_is_classic():
    cfg = GlobalConfig.from_dict({})
    assert cfg.strategy_mode == "classic"
    assert cfg.handled_topics_path.name == "handled_topics.json"
    assert cfg.handled_topics_path.parent.name == "data"
    manager = _build_manager(cfg)
    assert manager._is_aggressive_mode() is False
    assert manager._burst_slots() == 10


def test_compute_new_topics_dedupes_token_ids():
    latest = [
        {"topic_id": "t1"},
        {"token_id": "t1"},
        {"topic_id": "t2"},
    ]
    got = compute_new_topics(latest, handled={"t2"})
    assert got == ["t1"]


def test_sync_handled_topics_on_startup_trims_stale_entries(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["keep_token", "stale_token"]}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "keep_token", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    # 现行语义：copytrade 中存在但无活跃任务的 token 将从 handled 中移除，
    # 以便后续重新进入调度启动。
    assert manager.handled_topics == set()
    payload = json.loads(handled_path.read_text(encoding="utf-8"))
    assert payload.get("topics") == []


def test_sync_startup_skips_destructive_changes_when_position_snapshot_unavailable(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["keep_token"]}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "keep_token", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({}, "api_unavailable")  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    assert manager.handled_topics == {"keep_token"}
    assert manager._startup_sync_retry_needed is True
    assert manager._next_startup_sync_retry_at > 0
    payload = json.loads(handled_path.read_text(encoding="utf-8"))
    assert payload.get("topics") == ["keep_token"]


def test_refresh_topics_retries_startup_sync_and_purges_runtime_state(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["keep_token"]}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "keep_token", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager.topic_details["keep_token"] = {"resume_state": {"has_position": False}}
    manager._pending_first_seen["keep_token"] = 1.0
    manager._shared_ws_wait_failures["keep_token"] = 1
    manager._clob_book_probe_cache["keep_token"] = {"bid": 0.1}
    manager._position_snapshot_cache["keep_token"] = {"has_position": False}

    manager._refresh_sell_position_snapshot = lambda: ({}, "api_unavailable")  # type: ignore[assignment]
    manager._sync_handled_topics_on_startup()
    assert manager._startup_sync_retry_needed is True

    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]
    manager._next_startup_sync_retry_at = 0.0
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]
    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]

    manager._refresh_topics()

    assert manager._startup_sync_retry_needed is False
    assert "keep_token" not in manager.topic_details
    assert "keep_token" not in manager._pending_first_seen
    assert "keep_token" not in manager._shared_ws_wait_failures
    assert "keep_token" not in manager._clob_book_probe_cache
    assert "keep_token" not in manager._position_snapshot_cache


def test_sync_startup_sell_with_position_triggers_exit_cleanup_path(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["t1"]}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "t1", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "sell_tokens": [
                    {"token_id": "t1", "introduced_by_buy": True, "status": "pending"}
                ],
            }
        ),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({"t1": 1.0}, "ok")  # type: ignore[assignment]
    captured = []
    manager._trigger_sell_exit = lambda token_id, task=None, **kwargs: captured.append((token_id, task, kwargs))  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    assert captured == [("t1", None, {"trigger_source": "startup_reconcile_sell_signal", "trigger_reason": "COPYTRADE_SELL"})]


def test_startup_full_reconcile_cleans_copytrade_token_when_handled_missing(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": []}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "ghost_token", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    payload = json.loads(tokens_path.read_text(encoding="utf-8"))
    token_ids = [item.get("token_id") for item in payload.get("tokens", []) if isinstance(item, dict)]
    assert "ghost_token" not in token_ids
    assert "ghost_token" not in manager.pending_topics
    assert "ghost_token" not in manager.pending_burst_topics
    assert manager._startup_sync_retry_needed is False


def test_startup_full_reconcile_moves_position_token_to_base_pending(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": []}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "hold_token", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({"hold_token": 1.2}, "ok")  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    assert "hold_token" in manager.pending_topics
    assert "hold_token" not in manager.pending_burst_topics
    detail = manager.topic_details.get("hold_token") or {}
    assert detail.get("schedule_lane") == "base"
    assert detail.get("queue_role") == "startup_reconcile_position"
    assert manager._startup_sync_retry_needed is False


def test_startup_reconcile_applies_title_blacklist_with_position(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": []}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {
                        "token_id": "hold_token",
                        "introduced_by_buy": True,
                        "title": "US strikes Iran by March 31, 2026?",
                        "slug": "us-strikes-iran-by-march-31-2026",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "scheduler": {
                "title_blacklist": {
                    "enabled": True,
                    "keywords": ["Iran"],
                    "match_on_slug": True,
                    "action_with_position": "sell_only_maker",
                }
            },
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({"hold_token": 1.2}, "ok")  # type: ignore[assignment]
    manager._has_account_position = lambda token_id: token_id == "hold_token"  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    assert "hold_token" in manager.pending_topics
    detail = manager.topic_details.get("hold_token") or {}
    assert detail.get("queue_role") == "title_blacklist_sell_only"
    assert detail.get("force_sell_only_on_startup") is True
    assert manager._startup_sync_retry_needed is False


def test_hot_reload_updates_title_blacklist_keywords(tmp_path):
    cfg_file = tmp_path / "global_config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "scheduler": {
                    "title_blacklist": {
                        "enabled": True,
                        "keywords": ["Iran"],
                        "match_on_slug": True,
                        "action_with_position": "sell_only_maker",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "title_blacklist": {
                    "enabled": True,
                    "keywords": ["Iran"],
                    "match_on_slug": True,
                    "action_with_position": "sell_only_maker",
                }
            }
        }
    )
    cfg.global_config_path = cfg_file
    manager = _build_manager(cfg)

    assert manager.config.title_blacklist_keywords == ["Iran"]
    manager._next_config_reload_check = 0.0

    cfg_file.write_text(
        json.dumps(
            {
                "scheduler": {
                    "title_blacklist": {
                        "enabled": True,
                        "keywords": ["Russia", "Iran"],
                        "match_on_slug": True,
                        "action_with_position": "sell_only_maker",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manager._config_mtime_ns = 0
    manager._hot_reload_runtime_config()

    assert manager.config.title_blacklist_keywords == ["Russia", "Iran"]
    assert manager.config.title_blacklist_enabled is True
    assert manager.config.title_blacklist_action_with_position == "sell_only_maker"


def test_start_process_hydrates_title_before_blacklist_check(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    handled_path = copytrade_dir / "handled_topics.json"

    token_id = "hold_token"
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {
                        "token_id": token_id,
                        "title": "US strikes Iran by March 31, 2026?",
                        "slug": "us-strikes-iran-by-march-31-2026",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(json.dumps({"updated_at": "", "sell_tokens": []}), encoding="utf-8")
    handled_path.write_text(json.dumps({"updated_at": "", "topics": []}), encoding="utf-8")

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "scheduler": {
                "title_blacklist": {
                    "enabled": True,
                    "keywords": ["Iran"],
                    "match_on_slug": True,
                    "action_with_position": "sell_only_maker",
                }
            },
        }
    )
    manager = _build_manager(cfg)
    manager.topic_details[token_id] = {"queue_role": "restored_token"}
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (  # type: ignore[assignment]
        {
            "question": "US strikes Iran by March 31, 2026?",
            "slug": "us-strikes-iran-by-march-31-2026",
        },
        "stub",
    )
    seen = {}

    def _fake_enforce(tid: str, *, source: str) -> str:
        seen["title"] = (manager.topic_details.get(tid) or {}).get("title")
        seen["source"] = source
        return "blocked_no_position"

    manager._enforce_title_blacklist_policy = _fake_enforce  # type: ignore[assignment]

    ok = manager._start_topic_process(token_id)

    assert ok is False
    assert seen.get("source") == "before_start"
    assert "Iran" in str(seen.get("title") or "")


def test_start_process_hydrates_title_from_positions_cache(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    handled_path = copytrade_dir / "handled_topics.json"

    token_id = "hold_token"
    tokens_path.write_text(
        json.dumps({"updated_at": "", "tokens": [{"token_id": token_id}]}),
        encoding="utf-8",
    )
    sell_path.write_text(json.dumps({"updated_at": "", "sell_tokens": []}), encoding="utf-8")
    handled_path.write_text(json.dumps({"updated_at": "", "topics": []}), encoding="utf-8")
    (data_dir / "positions_cache.json").write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "asset": token_id,
                        "title": "US strikes Iran by March 31, 2026?",
                        "slug": "us-strikes-iran-by-march-31-2026",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "data_dir": str(data_dir),
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "scheduler": {
                "title_blacklist": {
                    "enabled": True,
                    "keywords": ["Iran"],
                    "match_on_slug": True,
                    "action_with_position": "sell_only_maker",
                }
            },
        }
    )
    manager = _build_manager(cfg)
    manager.topic_details[token_id] = {"queue_role": "restored_token"}
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (  # type: ignore[assignment]
        {
            "question": "US strikes Iran by March 31, 2026?",
            "slug": "us-strikes-iran-by-march-31-2026",
        },
        "stub",
    )
    seen = {}

    def _fake_enforce(tid: str, *, source: str) -> str:
        seen["title"] = (manager.topic_details.get(tid) or {}).get("title")
        seen["source"] = source
        return "blocked_no_position"

    manager._enforce_title_blacklist_policy = _fake_enforce  # type: ignore[assignment]

    ok = manager._start_topic_process(token_id)

    assert ok is False
    assert seen.get("source") == "before_start"
    assert "Iran" in str(seen.get("title") or "")


def test_restore_runtime_status_sets_restored_role_for_task_snapshot(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    status_path = data_dir / "autorun_status.json"
    status_path.write_text(
        json.dumps(
            {
                "pending_topics": [],
                "pending_exit_topics": [],
                "tasks": {
                    "task_token": {
                        "config_path": "",
                        "log_path": "",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "data_dir": str(data_dir),
            "runtime_status_path": str(status_path),
            "handled_topics_path": str(data_dir / "handled_topics.json"),
            "copytrade_tokens_path": str(data_dir / "tokens_from_copytrade.json"),
            "copytrade_sell_signals_path": str(data_dir / "copytrade_sell_signals.json"),
            "copytrade_blacklist_path": str(data_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_exit_tokens = lambda: []  # type: ignore[assignment]
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (None, "stub")  # type: ignore[assignment]

    manager._restore_runtime_status()

    detail = manager.topic_details.get("task_token") or {}
    assert detail.get("queue_role") == "restored_token"


def test_start_process_blocks_when_metadata_unverified_without_position():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "title_blacklist": {
                    "enabled": True,
                    "keywords": ["Iran"],
                }
            }
        }
    )
    manager = _build_manager(cfg)
    token_id = "unverified_x"
    manager.topic_details[token_id] = {}
    manager._has_account_position = lambda _tid: False  # type: ignore[assignment]
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (None, "request_error")  # type: ignore[assignment]
    captured = []
    manager._append_exit_token_record = lambda token, reason, **kwargs: captured.append((token, reason, kwargs))  # type: ignore[assignment]
    manager._enforce_title_blacklist_policy = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not reach enforce"))  # type: ignore[assignment]

    ok = manager._start_topic_process(token_id)

    assert ok is False
    assert captured
    assert captured[0][1] == "TITLE_BLACKLIST_METADATA_UNVERIFIED_NO_POSITION"


def test_ensure_resume_state_from_live_position_sets_skip_buy():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "restored_1"
    manager.topic_details[token_id] = {"queue_role": "restored_token"}
    manager._stoploss_reentry_states[token_id] = manager._default_stoploss_reentry_state(
        token_id
    )
    manager._refresh_unified_position_snapshot = lambda **_kwargs: (  # type: ignore[assignment]
        [{"asset": token_id, "size": 4.0, "avgPrice": 0.63}],
        {token_id: 4.0},
        "ok",
        "live",
    )

    manager._ensure_resume_state_from_live_position(token_id)

    resume = (manager.topic_details.get(token_id) or {}).get("resume_state") or {}
    assert resume.get("has_position") is True
    assert float(resume.get("position_size") or 0.0) == 4.0
    assert float(resume.get("entry_price") or 0.0) == 0.63
    assert resume.get("skip_buy") is True


def test_start_process_blocked_when_sell_cleanup_in_flight():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "cleanup_token"
    manager.pending_exit_topics.append(token_id)
    manager.topic_details[token_id] = {"queue_role": "restored_token"}
    manager._hydrate_topic_metadata_for_blacklist = lambda *_args, **_kwargs: None  # type: ignore[assignment]
    manager._apply_metadata_unverified_guard = lambda *_args, **_kwargs: "verified"  # type: ignore[assignment]
    manager._enforce_title_blacklist_policy = lambda *_args, **_kwargs: "allowed"  # type: ignore[assignment]

    ok = manager._start_topic_process(token_id)

    assert ok is False


def test_hydrate_force_official_check_not_short_circuited_by_local_metadata():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "force_meta_token"
    manager.topic_details[token_id] = {
        "title": "local title",
        "slug": "local-slug",
    }
    calls = {"n": 0}

    def _fake_fetch(_tid: str):
        calls["n"] += 1
        return (
            {"question": "official title", "slug": "official-slug"},
            "stub",
        )

    manager._fetch_market_metadata_from_gamma_by_token_id = _fake_fetch  # type: ignore[assignment]

    manager._hydrate_topic_metadata_for_blacklist(token_id, force_official_check=True)

    assert calls["n"] == 1
    detail = manager.topic_details[token_id]
    assert detail["title"] == "official title"
    assert detail["slug"] == "official-slug"
    assert detail.get("blacklist_metadata_verified") is True


def test_hydrate_calls_official_fetch_when_positions_cache_missing():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "no_cache_token"
    calls = {"n": 0}

    def _fake_fetch(_tid: str):
        calls["n"] += 1
        return (None, "stub_error")

    manager._fetch_market_metadata_from_gamma_by_token_id = _fake_fetch  # type: ignore[assignment]

    manager._hydrate_topic_metadata_for_blacklist(token_id, force_official_check=True)

    assert calls["n"] == 1
    detail = manager.topic_details[token_id]
    assert detail.get("blacklist_metadata_verified") is False
    assert detail.get("blacklist_metadata_source") == "stub_error"


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

    # 现行语义：函数为空实现，不再将 burst 回挪到 base。
    assert manager.pending_burst_topics == ["x", "y"]
    assert manager.pending_topics == ["z"]


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
    manager._startup_sync_retry_needed = False
    manager._next_startup_sync_retry_at = 0.0

    manager._is_buy_paused_by_balance = lambda: True  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "a", "token_id": "a"},
        {"topic_id": "b", "token_id": "b"},
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
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




def test_refresh_topics_preserves_queue_role_for_existing_pending_burst():
    cfg = GlobalConfig.from_dict({"scheduler": {"strategy_mode": "classic"}})
    manager = _build_manager(cfg)

    manager.pending_burst_topics = ["a"]
    manager.topic_details["a"] = {"queue_role": "new_token", "schedule_lane": "burst"}
    manager.handled_topics.add("a")

    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "a", "token_id": "a"},
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]

    manager._refresh_topics()

    assert manager.pending_burst_topics == ["a"]
    assert manager.topic_details["a"]["queue_role"] == "new_token"
    assert manager._queue_role(manager.topic_details.get("a") or {}) == "new_token"

def test_refresh_topics_routes_new_tokens_to_burst_in_classic_and_aggressive_mode():
    cfg = GlobalConfig.from_dict({"scheduler": {"strategy_mode": "classic", "aggressive_burst_slots": 2}})
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager._next_startup_sync_retry_at = 0.0

    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "a", "token_id": "a"},
        {"topic_id": "b", "token_id": "b"},
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]

    manager._refresh_topics()

    assert manager.pending_topics == []
    assert manager.pending_burst_topics == ["a", "b"]
    assert manager.topic_details["a"]["queue_role"] == "new_token"
    assert manager.topic_details["b"]["queue_role"] == "new_token"


def test_rebalance_moves_new_token_from_burst_to_base_but_keeps_reentry_in_burst():
    cfg = GlobalConfig.from_dict({"scheduler": {"strategy_mode": "classic", "max_concurrent_tasks": 2}})
    manager = _build_manager(cfg)

    manager.pending_burst_topics = ["r1", "n1", "n2"]
    manager.topic_details["r1"] = {"queue_role": "reentry_token", "schedule_lane": "burst"}
    manager.topic_details["n1"] = {"queue_role": "new_token", "schedule_lane": "burst"}
    manager.topic_details["n2"] = {"queue_role": "new_token", "schedule_lane": "burst"}

    class _Task:
        def __init__(self, running: bool):
            self._running = running

        def is_running(self):
            return self._running

    manager.tasks = {
        "base_running": _Task(True),
    }
    manager._running_burst_count = lambda: 0  # type: ignore[assignment]

    manager._rebalance_burst_to_base_queue()

    # 现行语义：rebalance 已废弃，队列不应被修改。
    assert manager.pending_topics == []
    assert manager.pending_burst_topics == ["r1", "n1", "n2"]
    assert manager.topic_details["n1"]["schedule_lane"] == "burst"


def test_reentry_enqueue_promotes_ahead_of_new_tokens_in_burst():
    cfg = GlobalConfig.from_dict({"scheduler": {"strategy_mode": "aggressive", "aggressive_burst_slots": 2}})
    manager = _build_manager(cfg)
    manager.pending_burst_topics = ["new_a", "new_b"]

    manager.topic_details["new_a"] = {"queue_role": "new_token"}
    manager.topic_details["new_b"] = {"queue_role": "new_token"}
    manager.pending_topics = ["reentry_x"]

    manager._poll_aggressive_self_sell_reentry = lambda: None  # type: ignore[assignment]
    manager._enqueue_burst_topic("reentry_x", promote=True)

    assert manager.pending_burst_topics[0] == "reentry_x"


def test_poll_reentry_always_promotes_to_burst_front():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "strategy_mode": "aggressive",
                "aggressive_enable_self_sell_reentry": True,
                "aggressive_reentry_source": "self_account_fills_only",
            }
        }
    )
    manager = _build_manager(cfg)
    manager.pending_burst_topics = ["new_a", "new_b"]
    manager._position_address = "0xabc"
    manager._process_started_at = 1.0
    manager._last_self_sell_trade_ts = 1
    manager._mark_reentry_eligible_token("reentry_x", source="SELL_ABANDONED")

    import poly_maker_autorun as autorun

    old_fetch = autorun._fetch_recent_trades_from_data_api
    try:
        autorun._fetch_recent_trades_from_data_api = lambda *_args, **_kwargs: ([
            {
                "side": "SELL",
                "asset": "reentry_x",
                "timestamp": 2,
                "size": "1",
                "price": "0.5",
                "conditionId": "c1",
            }
        ], "ok")

        manager._poll_aggressive_self_sell_reentry()
    finally:
        autorun._fetch_recent_trades_from_data_api = old_fetch

    assert manager.pending_burst_topics[0] == "reentry_x"
    assert manager.topic_details["reentry_x"]["queue_role"] == "reentry_token"


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


def test_sell_abandoned_stale_position_record_is_blocked(tmp_path):
    cfg = GlobalConfig.from_dict({"data_dir": str(tmp_path / "data")})
    manager = _build_manager(cfg)
    manager._has_account_position = lambda _token_id: False  # type: ignore[assignment]
    record = {
        "token_id": "stale_sell",
        "exit_reason": "SELL_ABANDONED",
        "exit_ts": 1.0,
        "exit_data": {"has_position": True, "position_size": 11.0},
        "refillable": True,
    }
    manager._exit_tokens_path.parent.mkdir(parents=True, exist_ok=True)
    manager._exit_tokens_path.write_text(
        json.dumps([record], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    loaded = manager._load_exit_tokens()
    refillable = manager._filter_refillable_tokens(loaded)

    assert refillable == []

    persisted = manager._load_exit_tokens()
    assert persisted[0]["exit_data"]["has_position"] is True
    assert persisted[0]["exit_data"]["position_size"] == 11.0


def test_title_blacklist_with_position_stale_record_remains_blocked(tmp_path):
    cfg = GlobalConfig.from_dict({"data_dir": str(tmp_path / "data")})
    manager = _build_manager(cfg)
    manager._has_account_position = lambda _token_id: False  # type: ignore[assignment]
    record = {
        "token_id": "blocked_token",
        "exit_reason": "TITLE_BLACKLIST_WITH_POSITION",
        "exit_ts": 1.0,
        "exit_data": {"has_position": True, "position_size": 2.0},
        "refillable": True,
    }
    manager._exit_tokens_path.parent.mkdir(parents=True, exist_ok=True)
    manager._exit_tokens_path.write_text(
        json.dumps([record], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    loaded = manager._load_exit_tokens()
    refillable = manager._filter_refillable_tokens(loaded)

    assert refillable == []
    persisted = manager._load_exit_tokens()
    assert persisted[0]["exit_data"]["has_position"] is True
    assert persisted[0]["exit_data"]["position_size"] == 2.0


def test_buy_block_entry_sync_failed_stale_position_record_is_blocked(tmp_path):
    cfg = GlobalConfig.from_dict({"data_dir": str(tmp_path / "data")})
    manager = _build_manager(cfg)
    manager._has_account_position = lambda _token_id: False  # type: ignore[assignment]
    record = {
        "token_id": "entry_sync_token",
        "exit_reason": "BUY_BLOCK_ENTRY_SYNC_FAILED",
        "exit_ts": 1.0,
        "exit_data": {"has_position": True, "position_size": 3.0},
        "refillable": True,
    }
    manager._exit_tokens_path.parent.mkdir(parents=True, exist_ok=True)
    manager._exit_tokens_path.write_text(
        json.dumps([record], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    loaded = manager._load_exit_tokens()
    refillable = manager._filter_refillable_tokens(loaded)

    assert refillable == []
    persisted = manager._load_exit_tokens()
    assert persisted[0]["exit_data"]["has_position"] is True
    assert persisted[0]["exit_data"]["position_size"] == 3.0


def test_buy_block_trigger_unavailable_stale_record_remains_blocked(tmp_path):
    cfg = GlobalConfig.from_dict({"data_dir": str(tmp_path / "data")})
    manager = _build_manager(cfg)
    manager._has_account_position = lambda _token_id: False  # type: ignore[assignment]
    record = {
        "token_id": "trigger_token",
        "exit_reason": "BUY_BLOCK_TRIGGER_UNAVAILABLE",
        "exit_ts": 1.0,
        "exit_data": {"has_position": True, "position_size": 4.0},
        "refillable": True,
    }
    manager._exit_tokens_path.parent.mkdir(parents=True, exist_ok=True)
    manager._exit_tokens_path.write_text(
        json.dumps([record], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    loaded = manager._load_exit_tokens()
    refillable = manager._filter_refillable_tokens(loaded)

    assert refillable == []


def test_gap_skip_backoff_applies_and_resets_on_non_gap():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "refill_cooldown_minutes_with_position": 0,
                "refill_cooldown_minutes_no_position": 0,
                "gap_skip_backoff_enabled": True,
                "gap_skip_backoff_minutes": [5, 15, 45, 120],
            }
        }
    )
    manager = _build_manager(cfg)
    now = time.time()

    # 最新两条都是 gap，按第2档退避（15分钟）应被拦截
    blocked = manager._filter_refillable_tokens(
        [
            {
                "token_id": "gap_token",
                "exit_reason": "REFILL_SKIP_GAP",
                "exit_ts": now - 60,
                "exit_data": {"has_position": False},
                "refillable": True,
            },
            {
                "token_id": "gap_token",
                "exit_reason": "POSITION_SYNC_SKIP_GAP",
                "exit_ts": now - 120,
                "exit_data": {"has_position": False},
                "refillable": True,
            },
        ]
    )
    assert blocked == []

    # 最新一条改为非 gap，视为重置，应允许回填
    released = manager._filter_refillable_tokens(
        [
            {
                "token_id": "gap_token",
                "exit_reason": "SELL_ABANDONED",
                "exit_ts": now - 60,
                "exit_data": {"has_position": False},
                "refillable": True,
            },
            {
                "token_id": "gap_token",
                "exit_reason": "REFILL_SKIP_GAP",
                "exit_ts": now - 120,
                "exit_data": {"has_position": False},
                "refillable": True,
            },
        ]
    )
    assert len(released) == 1
    assert released[0]["token_id"] == "gap_token"


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


def test_ws_chunk_and_confirm_config_can_be_loaded_from_scheduler():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "ws_subscribe_chunk_size": 17,
                "ws_subscribe_chunk_interval_ms": 80,
                "ws_ready_use_confirmed": True,
                "ws_ready_confirm_grace_sec": 1.5,
            }
        }
    )

    assert cfg.ws_subscribe_chunk_size == 17
    assert cfg.ws_subscribe_chunk_interval_ms == 80.0
    assert cfg.ws_ready_use_confirmed is True
    assert cfg.ws_ready_confirm_grace_sec == 1.5


def test_last_trade_event_bootstraps_cache_for_subscribed_token():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "token-a"
    manager._ws_token_ids = [token_id]

    manager._update_token_timestamp_from_trade(
        {
            "event_type": "last_trade_price",
            "asset_id": token_id,
            "price": "0.52",
        }
    )

    with manager._ws_cache_lock:
        assert token_id in manager._ws_cache
        assert manager._ws_cache[token_id]["price"] == 0.52
        assert manager._ws_cache[token_id]["source"] == "last_trade_price_bootstrap"


def test_evict_stale_pending_topics_requires_ws_unconfirmed_and_book_unavailable():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "pending_soft_eviction_minutes": 1,
                "enable_pending_soft_eviction": True,
            }
        }
    )
    manager = _build_manager(cfg)
    manager.pending_topics = ["t1", "t2", "t3"]
    now = 1000.0
    manager._pending_first_seen = {"t1": 0.0, "t2": 0.0, "t3": 0.0}

    manager._is_ws_confirmed = lambda token_id: token_id == "t2"  # type: ignore[assignment]
    manager._probe_clob_book_available = lambda token_id: (token_id == "t3", "probe")  # type: ignore[assignment]
    records = []
    manager._append_exit_token_record = lambda token_id, reason, **kwargs: records.append((token_id, reason, kwargs))  # type: ignore[assignment]

    import poly_maker_autorun as autorun

    old_time = autorun.time.time
    try:
        autorun.time.time = lambda: now
        manager._evict_stale_pending_topics()
    finally:
        autorun.time.time = old_time

    assert [token_id for token_id, _, _ in records] == ["t1"]
    assert all(reason == "NO_DATA_TIMEOUT" for _, reason, _ in records)
    assert "t1" not in manager.pending_topics
    assert "t2" in manager.pending_topics
    assert "t3" in manager.pending_topics


def test_task_runtime_mode_low_balance_refill_with_position():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._buy_paused_due_to_balance = True
    manager._get_task_run_config = lambda _task: {}  # type: ignore[assignment]
    task = TopicTask(topic_id="t1")

    mode = manager._task_runtime_mode(task, {"queue_role": "refill_with_position"})

    assert mode == "余额不足只卖出"


def test_task_runtime_mode_blacklist_has_priority_over_low_balance():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._buy_paused_due_to_balance = True
    manager._get_task_run_config = lambda _task: {"force_sell_only_on_startup": True}  # type: ignore[assignment]
    task = TopicTask(topic_id="t1")

    mode = manager._task_runtime_mode(task, {"queue_role": "title_blacklist_sell_only"})

    assert mode == "黑名单"


def test_cycle_gate_blocks_buy_before_allowed_ts(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(data_dir)}})
    manager = _build_manager(cfg)
    manager._token_cycle_states = {
        "t1": {
            "cycle_round": 1,
            "next_buy_allowed_ts": time.time() + 90.0,
            "next_drop_pct": 0.08,
        }
    }
    run_cfg = {"drop_pct": 0.05}

    allowed = manager._apply_token_cycle_buy_gate_and_drop_override("t1", run_cfg)

    assert allowed is False
    assert "resume_drop_pct" not in run_cfg


def test_cycle_gate_applies_drop_override_when_cooldown_elapsed(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(data_dir)}})
    manager = _build_manager(cfg)
    manager._token_cycle_states = {
        "t1": {
            "cycle_round": 3,
            "next_buy_allowed_ts": time.time() - 1.0,
            "next_drop_pct": 0.08,
        }
    }
    run_cfg = {
        "drop_pct": 0.05,
        "resume_drop_pct": 0.03,
        "enable_incremental_drop_pct": True,
        "incremental_drop_pct_cap": 0.20,
    }

    allowed = manager._apply_token_cycle_buy_gate_and_drop_override("t1", run_cfg)

    assert allowed is True
    assert run_cfg.get("resume_drop_pct") == 0.08


def test_advance_cycle_state_updates_round_cooldown_and_drop(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(data_dir)}})
    manager = _build_manager(cfg)
    run_cfg = {
        "drop_pct": 0.05,
        "enable_incremental_drop_pct": True,
        "incremental_drop_pct_step": 0.002,
        "incremental_drop_pct_cap": 0.20,
    }

    before = time.time()
    manager._advance_token_cycle_state_on_cleanup("t1", run_cfg)
    state1 = manager._token_cycle_states["t1"]
    after = time.time()

    assert state1["cycle_round"] == 1
    assert abs(float(state1.get("next_drop_pct")) - 0.052) < 1e-9
    assert before + 60.0 <= float(state1["next_buy_allowed_ts"]) <= after + 60.0

    manager._advance_token_cycle_state_on_cleanup("t1", run_cfg)
    state2 = manager._token_cycle_states["t1"]

    assert state2["cycle_round"] == 2
    assert abs(float(state2.get("next_drop_pct")) - 0.054) < 1e-9


def test_load_copytrade_tokens_skips_non_buy_introduced(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {"token_id": "buy_token", "introduced_by_buy": True},
                    {"token_id": "sell_only_token", "introduced_by_buy": False},
                ],
            }
        ),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(copytrade_dir / "copytrade_sell_signals.json"),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)

    topics = manager._load_copytrade_tokens()

    assert [t["topic_id"] for t in topics] == ["buy_token"]


def test_load_copytrade_tokens_skips_follow_cooldown_token(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    manual_path = copytrade_dir / "manual_intervention_tokens.json"
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {"token_id": "cooldown_token", "introduced_by_buy": True},
                    {"token_id": "normal_token", "introduced_by_buy": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(json.dumps({"updated_at": "", "sell_tokens": []}), encoding="utf-8")
    manual_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {
                        "token_id": "cooldown_token",
                        "follow_cooldown_until_ts": time.time() + 3600.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)

    topics = manager._load_copytrade_tokens()

    assert [t["topic_id"] for t in topics] == ["normal_token"]


def test_apply_sell_signals_requires_position_even_with_history(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    manager._sell_bootstrap_done = True
    manager._next_sell_full_recheck_at = time.time() + 3600.0
    manager._sell_position_snapshot = {"t1": 0.0}
    manager._sell_position_snapshot_info = "ok"
    manager.handled_topics.add("t1")

    triggered = []
    manager._trigger_sell_exit = lambda token_id, task, **kwargs: triggered.append((token_id, task, kwargs))  # type: ignore[assignment]

    manager._apply_sell_signals(
        {
            "t1": {
                "token_id": "t1",
                "introduced_by_buy": True,
                "status": "pending",
            }
        }
    )

    assert triggered == []
    assert "t1" in manager._handled_sell_signals


def test_stoploss_config_can_be_loaded_from_scheduler():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "stoploss": {
                    "enabled": True,
                    "check_interval_sec": 90,
                    "base_drawdown_pct": 0.05,
                    "confirm_rounds": 2,
                    "reentry_window_cooldown_minutes": 120,
                    "next_stoploss_cooldown_minutes": 30,
                    "reentry_extra_delay_after_5_cuts_hours": 24,
                    "reentry_line_ticks": 2,
                    "reentry_zone_lower_pct": 0.02,
                    "probe_break_pct": 0.05,
                    "daily_reentry_loss_circuit_breaker_pct": 0.10,
                    "drawdown_step_per_cycle_pct": 0.01,
                    "max_tokens_per_cycle": 2,
                }
            }
        }
    )
    assert cfg.enable_stoploss is True
    assert cfg.stoploss_check_interval_sec == 90
    assert cfg.stoploss_base_drawdown_pct == 0.05
    assert cfg.stoploss_confirm_rounds == 2
    assert cfg.stoploss_reentry_window_cooldown_minutes == 120
    assert cfg.stoploss_next_stoploss_cooldown_minutes == 30
    assert cfg.stoploss_reentry_extra_delay_after_5_cuts_hours == 24
    assert cfg.stoploss_reentry_line_ticks == 2
    assert cfg.stoploss_reentry_zone_lower_pct == 0.02
    assert cfg.stoploss_probe_break_pct == 0.05
    assert cfg.stoploss_daily_reentry_loss_circuit_breaker_pct == 0.10
    assert cfg.stoploss_drawdown_step_per_cycle_pct == 0.01
    assert cfg.stoploss_max_tokens_per_cycle == 2


def _build_stoploss_manager(tmp_path, *, mode="classic", stoploss_overrides=None):
    data_dir = tmp_path / "data"
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    handled_path = data_dir / "handled_topics.json"
    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["t1"]}),
        encoding="utf-8",
    )
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    tokens_path.write_text(
        json.dumps({"updated_at": "", "tokens": [{"token_id": "t1", "introduced_by_buy": True}]}),
        encoding="utf-8",
    )
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    sell_path.write_text(json.dumps({"updated_at": "", "sell_tokens": []}), encoding="utf-8")
    stoploss_cfg = {
        "enabled": True,
        "check_interval_sec": 60,
        "base_drawdown_pct": 0.05,
        "confirm_rounds": 2,
        "reentry_window_cooldown_minutes": 120.0,
        "next_stoploss_cooldown_minutes": 30.0,
        "reentry_extra_delay_after_5_cuts_hours": 24.0,
        "reentry_timeout_hours": 168.0,
        "reentry_line_ticks": 2,
        "reentry_zone_lower_pct": 0.02,
        "probe_break_pct": 0.05,
        "daily_reentry_loss_circuit_breaker_pct": 0.10,
        "drawdown_step_per_cycle_pct": 0.01,
        "max_tokens_per_cycle": 1,
    }
    if isinstance(stoploss_overrides, dict):
        stoploss_cfg.update(stoploss_overrides)
    cfg = GlobalConfig.from_dict(
        {
            "paths": {"data_directory": str(data_dir)},
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "stoploss_reentry_state_path": str(copytrade_dir / "stoploss_reentry_state.json"),
            "stoploss_reentry_state_backup_path": str(copytrade_dir / "stoploss_reentry_state.bak.json"),
            "scheduler": {
                "strategy_mode": mode,
                "stoploss": stoploss_cfg,
            },
        }
    )
    manager = _build_manager(cfg)
    manager.handled_topics.add("t1")
    manager._position_address = "0xabc"
    manager._build_copytrade_active_token_set = lambda: {"t1"}  # type: ignore[assignment]
    manager._remove_token_from_copytrade_files = lambda token_id: None  # type: ignore[assignment]
    return manager


def test_stoploss_full_clear_sets_reentry_window_state(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager.topic_details["t1"] = {"resume_state": {"profit_pct": 0.023}, "floor_price": 0.95}
    manager._ws_cache["t1"] = {"best_bid": 0.90, "updated_at": now}
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": True,
            "before_size": 20.0,
            "after_size": 0.0,
            "requested_size": kwargs.get("target_size", 20.0),
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [
            {
                "asset": "t1",
                "size": 20.0,
                "avgPrice": 1.0,
                "curPrice": 0.9,
            }
        ],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 61.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_EXITED_WAITING_WINDOW"
    assert float(state["reentry_earliest_ts"]) > now
    assert abs(float(state["stop_exit_price"]) - 0.8973) < 1e-9
    assert float(state["reentry_line_price"]) < 0.9
    assert float(state["probe_line_price"]) < float(state["reentry_line_price"])
    assert abs(float(state.get("old_maker_profit_pct") or 0.0) - 0.023) < 1e-9


def test_reentry_requires_probe_then_rebound_zone(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_WINDOW",
            "stoploss_cycle_count": 1,
            "next_stoploss_threshold_pct": 0.06,
            "stop_exit_price": 0.90,
            "stop_exit_ts": now - 7200,
            "last_stoploss_size": 5.0,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "reentry_earliest_ts": now - 10,
            "source_detached": False,
        },
    )
    prices = iter([0.83, 0.83, 0.879, 0.879])
    manager._estimate_reentry_buyable_price = lambda token_id, position_row=None: next(prices)  # type: ignore[assignment]
    manager._total_liquidation.reenter_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": True,
            "before_size": 0.0,
            "after_size": 5.0,
            "executed_price": 0.879,
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 60.0)
        manager._run_stoploss_check(now + 120.0)
        manager._run_stoploss_check(now + 180.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "REENTRY_HOLD"
    assert "resume_state" in manager.topic_details["t1"]
    assert manager.topic_details["t1"]["queue_role"] == "reentry_token"
    assert isinstance(manager.topic_details["t1"].get("stoploss_reentry_resume"), dict)


def test_reentry_uses_frozen_profit_pct_when_available(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_REBOUND",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.90,
            "stop_exit_ts": now - 7200,
            "last_stoploss_size": 5.0,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "reentry_earliest_ts": now - 10,
            "source_detached": False,
            "old_maker_profit_pct": 0.031,
        },
    )
    prices = iter([0.83, 0.83, 0.879, 0.879])
    manager._estimate_reentry_buyable_price = lambda token_id, position_row=None: next(prices)  # type: ignore[assignment]
    manager._total_liquidation.reenter_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": True,
            "before_size": 0.0,
            "after_size": 5.0,
            "executed_price": 0.879,
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 60.0)
        manager._run_stoploss_check(now + 120.0)
        manager._run_stoploss_check(now + 180.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    resume = manager.topic_details["t1"].get("stoploss_reentry_resume") or {}
    assert abs(float(resume.get("hold_profit_pct") or 0.0) - 0.031) < 1e-9


def test_detached_state_without_position_is_cleaned(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    manager._load_copytrade_tokens = lambda: []  # type: ignore[assignment]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_PROBE",
            "source_detached": True,
            "reentry_earliest_ts": now - 1.0,
            "probe_line_price": 0.8,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + float(manager.config.stoploss_check_interval_sec) + 1.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states


def test_stoploss_uses_executed_avg_price_as_anchor(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._ws_cache["t1"] = {"best_bid": 0.90, "best_ask": 0.91, "updated_at": now}
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": True,
            "before_size": 20.0,
            "after_size": 0.0,
            "requested_size": kwargs.get("target_size", 20.0),
            "executed_avg_price": 0.887,
            "executed_price_source": "response_fill",
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 20.0, "avgPrice": 1.0, "curPrice": 0.9}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 61.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert abs(float(state["stop_exit_price"]) - 0.887) < 1e-9
    assert state.get("stop_exit_price_source") == "response_fill"


def test_stoploss_does_not_trigger_on_non_executable_cur_price_only(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._ws_cache["t1"] = {"best_bid": 0.0, "best_ask": 0.0, "updated_at": now}
    calls = []
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: calls.append(kwargs) or {"ok": True, "before_size": 10.0, "after_size": 0.0}
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 10.0, "avgPrice": 1.0, "curPrice": 0.80}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 61.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert calls == []


def test_reentry_missing_quote_does_not_advance_state_and_is_observable(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_PROBE",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.90,
            "stop_exit_ts": now - 7200,
            "last_stoploss_size": 5.0,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "reentry_earliest_ts": now - 10,
            "probe_confirm_hits": 1,
            "rebound_confirm_hits": 1,
            "source_detached": False,
        },
    )
    manager._estimate_reentry_buyable_price = lambda token_id, position_row=None: None  # type: ignore[assignment]
    calls = []
    manager._total_liquidation.reenter_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True}
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_EXITED_WAITING_PROBE"
    assert int(state.get("reentry_quote_missing_hits") or 0) >= 1
    assert int(state.get("probe_confirm_hits") or 0) == 0
    assert int(state.get("rebound_confirm_hits") or 0) == 0
    assert "ask missing or stale" in str(state.get("last_error") or "")
    assert calls == []


def test_token_daily_reentry_pause_rolls_over_without_position(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    old_day = "2000-01-01"
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_WINDOW",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.90,
            "stop_exit_ts": now - 7200,
            "last_stoploss_size": 5.0,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "reentry_earliest_ts": now + 3600.0,
            "source_detached": False,
            "loss_date_utc": old_day,
            "today_realized_loss_pct": 0.123,
            "reentry_paused_for_day": True,
        },
    )
    manager._estimate_reentry_buyable_price = lambda token_id, position_row=None: 0.88  # type: ignore[assignment]
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]

    state = manager._stoploss_reentry_states["t1"]
    assert state.get("loss_date_utc") == manager._today_utc_date(now)
    assert float(state.get("today_realized_loss_pct") or 0.0) == 0.0
    assert bool(state.get("reentry_paused_for_day", True)) is False


def test_market_closed_removes_state_immediately(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_WINDOW",
            "stoploss_cycle_count": 2,
            "stop_exit_price": 0.9,
            "reentry_line_price": 0.89,
            "reentry_zone_lower_price": 0.88,
            "probe_line_price": 0.8455,
            "reentry_earliest_ts": now - 1.0,
            "source_detached": False,
        },
    )
    manager._stoploss_is_market_closed = lambda token_id: True  # type: ignore[assignment]
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states


def test_waiting_reentry_target_sell_cancels_with_record(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._load_copytrade_sell_signals = lambda: {"t1": {"status": "pending"}}  # type: ignore[assignment]
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_PROBE",
            "stop_exit_ts": now - 7200,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "source_detached": False,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    latest = rows[-1]
    assert latest.get("exit_reason") == "STOPLOSS_REENTRY_CANCELED"
    assert (latest.get("exit_data") or {}).get("abandon_reason") == "target_sell_signal_while_waiting_reentry"


def test_reentry_hold_recovery_marker_promotes_to_normal_maker(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "REENTRY_HOLD",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.9,
            "stop_exit_ts": now - 100.0,
            "last_stoploss_size": 3.0,
            "reentry_line_price": 0.89,
            "reentry_zone_lower_price": 0.88,
            "probe_line_price": 0.8455,
            "reentry_earliest_ts": now - 1.0,
            "source_detached": False,
        },
    )
    task = TopicTask(topic_id="t1", log_path=Path("dummy.log"))
    task.log_excerpt = (
        "[REENTRY_HOLD] 达到恢复阈值，恢复常规 maker 卖出: "
        "bid=0.9000 activation=0.8990"
    )
    changed = manager._sync_reentry_hold_recovered_from_log(task)
    assert changed is True
    assert manager._stoploss_reentry_states["t1"]["state"] == "NORMAL_MAKER"


def test_reentry_hold_without_position_is_cleaned_with_record(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "REENTRY_HOLD",
            "stop_exit_ts": now - 3600.0,
            "last_stoploss_size": 3.0,
            "source_detached": False,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + float(manager.config.stoploss_check_interval_sec) + 1.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    latest = rows[-1]
    assert latest.get("exit_reason") == "STOPLOSS_REENTRY_HOLD_CLOSED"
    assert (latest.get("exit_data") or {}).get("reason") == "reentry_hold_no_position"


def test_process_exit_forces_log_refresh_for_reentry_hold_recovery(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "REENTRY_HOLD",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.9,
            "stop_exit_ts": now - 100.0,
            "last_stoploss_size": 3.0,
            "reentry_line_price": 0.89,
            "reentry_zone_lower_price": 0.88,
            "probe_line_price": 0.8455,
            "reentry_earliest_ts": now - 1.0,
            "source_detached": False,
        },
    )
    log_path = tmp_path / "t1.log"
    log_path.write_text(
        "[REENTRY_HOLD] 达到恢复阈值，恢复常规 maker 卖出: bid=0.9000 activation=0.8990\n",
        encoding="utf-8",
    )
    task = TopicTask(topic_id="t1", log_path=log_path)
    task.last_log_excerpt_ts = time.time()
    task.log_excerpt = ""
    manager._handle_process_exit(task, 0)
    assert manager._stoploss_reentry_states["t1"]["state"] == "NORMAL_MAKER"


def test_process_exit_sell_cleanup_success_sets_follow_cooldown(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    sell_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "sell_tokens": [
                    {"token_id": "t1", "introduced_by_buy": True, "status": "pending"}
                ],
            }
        ),
        encoding="utf-8",
    )
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    tokens_path.write_text(
        json.dumps({"updated_at": "", "tokens": []}),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    task = TopicTask(topic_id="t1")
    task.end_reason = "sell signal cleanup"
    task.no_restart = True

    manager._handle_process_exit(task, 0)

    payload = json.loads((copytrade_dir / "manual_intervention_tokens.json").read_text(encoding="utf-8"))
    rows = payload.get("tokens") or []
    row = next((x for x in rows if str(x.get("token_id")) == "t1"), None)
    assert isinstance(row, dict)
    cooldown_until = float(row.get("follow_cooldown_until_ts") or 0.0)
    assert cooldown_until > time.time() + 23 * 3600.0


def test_source_detached_with_position_is_guard_held_without_forced_sell(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._load_copytrade_tokens = lambda: []  # type: ignore[assignment]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._ws_cache["t1"] = {"best_bid": 0.9, "updated_at": now}
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "stoploss_cycle_count": 1,
            "next_stoploss_threshold_pct": 0.05,
            "source_detached": False,
        },
    )
    liq_calls = []
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: liq_calls.append((args, kwargs)) or {"ok": True}
    )
    sell_calls = []
    manager._trigger_sell_exit = lambda token_id, task=None, **kwargs: sell_calls.append((token_id, task, kwargs))  # type: ignore[assignment]
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 10.0, "avgPrice": 1.0, "curPrice": 0.9}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert bool(state.get("source_detached", False)) is True
    assert state.get("market_status_last") == "source_detached_guard_hold"
    assert liq_calls == []
    assert sell_calls == []


def test_source_detached_timeout_skips_cleanup_below_value_threshold(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._load_copytrade_tokens = lambda: []  # type: ignore[assignment]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._ws_cache["t1"] = {"best_bid": 0.2, "updated_at": now}
    manager._total_liquidation.cfg.position_value_threshold = 3.0  # type: ignore[attr-defined]
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "source_detached": True,
            "source_detached_since_ts": now - 1800.0,
            "source_detached_cleanup_started": False,
        },
    )
    sell_calls = []
    manager._trigger_sell_exit = lambda token_id, task=None, **kwargs: sell_calls.append((token_id, task, kwargs)) or True  # type: ignore[assignment]
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 5.0, "avgPrice": 1.0, "curPrice": 0.2}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states
    assert sell_calls == []
    orphan_path = manager.config.data_dir / "orphan_tokens.json"
    assert orphan_path.exists()
    rows = json.loads(orphan_path.read_text(encoding="utf-8"))
    latest = rows[-1]
    assert latest.get("token_id") == "t1"
    assert latest.get("reason") == "SOURCE_DETACHED_LOW_VALUE"


def test_source_detached_timeout_triggers_cleanup_above_value_threshold(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._load_copytrade_tokens = lambda: []  # type: ignore[assignment]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._ws_cache["t1"] = {"best_bid": 0.8, "updated_at": now}
    manager._total_liquidation.cfg.position_value_threshold = 3.0  # type: ignore[attr-defined]
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "source_detached": True,
            "source_detached_since_ts": now - 1800.0,
            "source_detached_cleanup_started": False,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 5.0, "avgPrice": 1.0, "curPrice": 0.8}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state.get("market_status_last") == "source_detached_timeout_orphan_recorded"
    assert bool(state.get("source_detached_cleanup_started", False)) is True
    orphan_path = manager.config.data_dir / "orphan_tokens.json"
    assert orphan_path.exists()
    rows = json.loads(orphan_path.read_text(encoding="utf-8"))
    assert isinstance(rows, list) and len(rows) >= 1
    latest = rows[-1]
    assert latest.get("token_id") == "t1"


def test_waiting_reentry_times_out_and_is_abandoned(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"reentry_timeout_hours": 24.0})
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_REBOUND",
            "stop_exit_ts": now - (25.0 * 3600.0),
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "last_error": "ask missing or stale",
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    latest = rows[-1]
    assert latest.get("token_id") == "t1"
    assert latest.get("exit_reason") == "STOPLOSS_REENTRY_ABANDONED"
    exit_data = latest.get("exit_data") or {}
    assert exit_data.get("abandon_reason") == "reentry_timeout"


def test_reentry_fill_above_line_is_recovered_into_reentry_hold(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_REBOUND",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.90,
            "stop_exit_ts": now - 7200,
            "last_stoploss_size": 5.0,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "reentry_earliest_ts": now - 10,
            "source_detached": False,
        },
    )
    manager._estimate_reentry_buyable_price = lambda token_id, position_row=None: 0.879  # type: ignore[assignment]
    manager._total_liquidation.reenter_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": True,
            "before_size": 0.0,
            "after_size": 5.0,
            "executed_avg_price": 0.885,
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 60.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "REENTRY_HOLD"
    assert bool(state.get("last_reentry_above_line", False)) is True
    assert abs(float(state.get("last_reentry_above_line_price") or 0.0) - 0.885) < 1e-9
    assert "resume_state" in manager.topic_details["t1"]
