from __future__ import annotations

import json
import tempfile
import types
import time
from pathlib import Path
from types import SimpleNamespace

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from total_liquidation_manager import TotalLiquidationManager


class _Task:
    def __init__(self, running: bool, log_excerpt: str = ""):
        self._running = running
        self.log_excerpt = log_excerpt
        self.last_log_excerpt_ts = 0.0

    def is_running(self) -> bool:
        return self._running


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Autorun:
    def __init__(self, cfg, running_tasks: int, log_excerpt: str = ""):
        self.config = cfg
        self.tasks = {str(i): _Task(True, log_excerpt=log_excerpt) for i in range(running_tasks)}
        self._ws_cache_lock = _Lock()
        self._ws_cache = {}
        self.pending_topics = []
        self.pending_exit_topics = []


def _build_cfg(tmp: Path, enable: bool = True):
    return SimpleNamespace(
        data_dir=tmp / "data",
        log_dir=tmp / "logs",
        max_concurrent_tasks=10,
        total_liquidation={
            "enable_total_liquidation": enable,
            "min_interval_hours": 72,
            "trigger": {
                "idle_slot_ratio_threshold": 0.5,
                "idle_slot_duration_minutes": 1,
                "startup_grace_hours": 6,
                "no_trade_duration_minutes": 1,
                "min_free_balance": 20,
                "require_conditions": 2,
            },
            "liquidation": {
                "taker_slippage_bps": 30,
            },
            "reset": {
                "hard_reset_enabled": True,
                "remove_logs": True,
                "remove_json_state": True,
            },
        },
    )


def test_disabled_never_triggers():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=False)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        autorun = _Autorun(cfg, running_tasks=0)
        metrics = mgr.update_metrics(autorun)
        ok, reasons = mgr.should_trigger(metrics)
        assert ok is False
        assert reasons == []


def test_trigger_with_two_conditions_and_interval_guard():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        mgr.cfg.startup_grace_hours = 0
        autorun = _Autorun(cfg, running_tasks=0)

        mgr._idle_since = time.time() - 120
        mgr._last_trade_activity_ts = time.time() - 120
        metrics = mgr.update_metrics(autorun)
        ok, reasons = mgr.should_trigger(metrics)
        assert ok is True
        assert len(reasons) >= 2

        mgr._save_state({"last_trigger_ts": time.time()})
        ok2, _ = mgr.should_trigger(metrics)
        assert ok2 is False


def test_hard_reset_preserves_copytrade_config_and_clears_state_files():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        project_root = base / "POLYMARKET_MAKER_AUTO"
        copytrade_dir = base / "copytrade"
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        copytrade_dir.mkdir(parents=True, exist_ok=True)

        # data/log 文件
        (cfg.log_dir / "a.log").write_text("x", encoding="utf-8")
        (cfg.data_dir / "autorun_status.json").write_text("{}", encoding="utf-8")

        # copytrade：配置文件应保留，状态文件应清空重建
        (copytrade_dir / "copytrade_config.json").write_text('{"targets":[1]}', encoding="utf-8")
        (copytrade_dir / "tokens_from_copytrade.json").write_text('{"tokens":[{"token_id":"1"}]}', encoding="utf-8")
        (copytrade_dir / "copytrade_sell_signals.json").write_text('{"sell_tokens":[{"token_id":"1"}]}', encoding="utf-8")
        (copytrade_dir / "copytrade_state.json").write_text('{"targets":{"a":1}}', encoding="utf-8")

        mgr = TotalLiquidationManager(cfg, project_root)
        autorun = _Autorun(cfg, running_tasks=0)
        mgr._hard_reset_files(autorun)

        assert (copytrade_dir / "copytrade_config.json").exists()
        assert not (cfg.log_dir / "a.log").exists()
        assert not (cfg.data_dir / "autorun_status.json").exists()

        tokens_payload = json.loads((copytrade_dir / "tokens_from_copytrade.json").read_text(encoding="utf-8"))
        assert tokens_payload.get("tokens") == []
        signals_payload = json.loads((copytrade_dir / "copytrade_sell_signals.json").read_text(encoding="utf-8"))
        assert signals_payload.get("sell_tokens") == []
        state_payload = json.loads((copytrade_dir / "copytrade_state.json").read_text(encoding="utf-8"))
        assert state_payload.get("targets") == {}


def test_taker_price_applies_slippage_bps():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        px = mgr._compute_taker_price(bid=0.5, ask=0.51)
        assert abs(px - 0.5 * (1 - 0.003)) < 1e-9




def _install_fake_balance_types_module():
    import types

    fake_mod = types.ModuleType("py_clob_client.clob_types")

    class _AssetType:
        COLLATERAL = "COLLATERAL"

    class _BalanceAllowanceParams:
        def __init__(self, asset_type=None, token_id=None, signature_type=-1):
            self.asset_type = asset_type
            self.token_id = token_id
            self.signature_type = signature_type

    fake_mod.AssetType = _AssetType
    fake_mod.BalanceAllowanceParams = _BalanceAllowanceParams
    old = sys.modules.get("py_clob_client.clob_types")
    sys.modules["py_clob_client.clob_types"] = fake_mod
    return old


def _restore_fake_balance_types_module(old):
    if old is None:
        sys.modules.pop("py_clob_client.clob_types", None)
    else:
        sys.modules["py_clob_client.clob_types"] = old


def test_balance_probe_is_rate_limited():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        cfg.total_liquidation["trigger"]["balance_poll_interval_sec"] = 9999
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        class _FakeClient:
            def __init__(self):
                self.calls = 0

            def get_balance_allowance(self, params):
                self.calls += 1
                return {"balance": "100"}

        fake = _FakeClient()
        mgr._cached_client = fake
        autorun = _Autorun(cfg, running_tasks=0)

        old_mod = _install_fake_balance_types_module()
        try:
            v1 = mgr._query_free_balance_usdc(autorun)
            v2 = mgr._query_free_balance_usdc(autorun)
        finally:
            _restore_fake_balance_types_module(old_mod)

        assert v1 == 100.0
        assert v2 == 100.0
        assert fake.calls == 1


def test_startup_grace_blocks_idle_trigger():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        cfg.total_liquidation["trigger"]["startup_grace_hours"] = 6
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        autorun = _Autorun(cfg, running_tasks=0)

        mgr._idle_since = time.time() - 3600
        mgr._last_trade_activity_ts = time.time()
        metrics = mgr.update_metrics(autorun)
        ok, reasons = mgr.should_trigger(metrics)
        assert ok is False
        assert all("idle_slots" not in r for r in reasons)




def test_trade_activity_refreshes_even_if_last_line_text_repeats():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        autorun = _Autorun(cfg, running_tasks=1, log_excerpt="[MAKER][BUY] 挂单 -> price=0.33 qty=5")
        task = autorun.tasks["0"]
        task.last_log_excerpt_ts = 100.0

        t1 = mgr._collect_trade_activity_ts(autorun)
        assert t1 > 0

        # 文本不变，但时间戳更新，说明是新一轮日志刷新，应被视为活动
        task.last_log_excerpt_ts = 101.0
        t2 = mgr._collect_trade_activity_ts(autorun)
        assert t2 > 0


def test_trade_activity_is_based_on_order_behavior_not_ws_updates():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        mgr.cfg.startup_grace_hours = 0
        mgr.cfg.no_trade_duration_minutes = 1

        # 先让无交易活动超时
        autorun = _Autorun(cfg, running_tasks=1, log_excerpt="[DIAG] no-op")
        mgr._last_trade_activity_ts = time.time() - 120
        metrics = mgr.update_metrics(autorun)
        _, reasons = mgr.should_trigger(metrics)
        assert any("no_trade_for=" in r for r in reasons)

        # 出现真实下单行为后，no_trade 应清零
        autorun.tasks["0"].log_excerpt = "[MAKER][BUY] 挂单 -> price=0.33 qty=5.0000"
        metrics2 = mgr.update_metrics(autorun)
        _, reasons2 = mgr.should_trigger(metrics2)
        assert all("no_trade_for=" not in r for r in reasons2)


def test_liquidation_scope_only_copytrade_tokens():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        project_root = base / "POLYMARKET_MAKER_AUTO"
        copytrade_dir = base / "copytrade"
        copytrade_dir.mkdir(parents=True, exist_ok=True)
        (copytrade_dir / "tokens_from_copytrade.json").write_text(
            '{"tokens":[{"token_id":"A"}]}' , encoding="utf-8"
        )
        (copytrade_dir / "copytrade_sell_signals.json").write_text(
            '{"sell_tokens":[]}', encoding="utf-8"
        )

        mgr = TotalLiquidationManager(cfg, project_root)

        fake_mod = types.ModuleType("maker_execution")
        fake_mod.maker_sell_follow_ask_with_floor_wait = lambda **kwargs: {"status": "FILLED"}
        sys.modules["maker_execution"] = fake_mod

        mgr._fetch_positions = lambda: [
            {"token_id": "A", "size": 10, "price": 0.5},
            {"token_id": "B", "size": 10, "price": 0.5},
        ]

        called = []
        mgr._place_sell_ioc = lambda client, token_id, price, size: called.append(token_id) or {}

        class _Client:
            pass

        mgr._cached_client = _Client()

        autorun = _Autorun(cfg, running_tasks=0)
        result = mgr._liquidate_positions(autorun)
        assert result["liquidated"] == 1
        assert called == ["A"]




def test_balance_parser_ignores_allowance_only_payload():
    payload = {"allowance": "5000", "nested": {"x": "999"}}
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed is None




def test_balance_parser_supports_nested_balance_amount_payload():
    payload = {"balance": {"amount": "1.38"}, "allowance": "5000"}
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed == 1.38


def test_balance_parser_supports_balance_list_payload():
    payload = {"balance": ["1.38"], "allowance": "5000"}
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed == 1.38




def test_balance_parser_rejects_allowance_inside_balance_subtree():
    payload = {"balance": {"allowance": "5000"}, "foo": "1"}
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed is None


def test_balance_parser_supports_balance_list_of_objects_payload():
    payload = {"balance": [{"allowance": "5000"}, {"amount": "1.38"}]}
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed == 1.38

def test_balance_parser_prefers_balance_field_over_other_numeric_values():
    payload = {
        "allowance": "5000",
        "nested": {"x": "999"},
        "balance": "1.38",
    }
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed == 1.38


def test_balance_query_does_not_call_none_fallback_on_exception():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        class _FakeClient:
            def __init__(self):
                self.calls = 0

            def get_balance_allowance(self, params):
                self.calls += 1
                raise RuntimeError("boom")

        fake = _FakeClient()
        mgr._cached_client = fake
        mgr._cached_free_balance = 7.0
        autorun = _Autorun(cfg, running_tasks=0)

        old_mod = _install_fake_balance_types_module()
        try:
            v = mgr._query_free_balance_usdc(autorun)
        finally:
            _restore_fake_balance_types_module(old_mod)

        assert v == 7.0
        assert fake.calls == 1
        assert mgr._last_balance_probe_error == "boom"


def test_liquidation_scope_empty_skips_for_safety():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        project_root = base / "POLYMARKET_MAKER_AUTO"
        mgr = TotalLiquidationManager(cfg, project_root)

        fake_mod = types.ModuleType("maker_execution")
        fake_mod.maker_sell_follow_ask_with_floor_wait = lambda **kwargs: {"status": "FILLED"}
        sys.modules["maker_execution"] = fake_mod

        mgr._fetch_positions = lambda: [{"token_id": "A", "size": 10, "price": 0.5}]

        called = []
        mgr._place_sell_ioc = lambda client, token_id, price, size: called.append(token_id) or {}

        class _Client:
            pass

        mgr._cached_client = _Client()

        autorun = _Autorun(cfg, running_tasks=0)
        result = mgr._liquidate_positions(autorun)
        assert result["liquidated"] == 0
        assert called == []
        assert any("scope is empty" in err for err in result["errors"])


def test_execute_aborted_does_not_hard_reset_or_stop():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")

        class _Evt:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        class _Run:
            def __init__(self):
                self.pending_topics = ["x"]
                self.pending_exit_topics = ["y"]
                self.stop_event = _Evt()

            def _stop_ws_aggregator(self):
                return None

            def _cleanup_all_tasks(self):
                return None

        autorun = _Run()

        hard_reset_called = {"v": False}
        mgr._hard_reset_files = lambda _a: hard_reset_called.__setitem__("v", True)
        mgr._liquidate_positions = lambda _a: {
            "liquidated": 0,
            "maker_count": 0,
            "taker_count": 0,
            "errors": ["copytrade token scope is empty; skip liquidation for safety"],
            "aborted": True,
        }

        result = mgr.execute(autorun, ["cond_a", "cond_b"])
        assert result["hard_reset"] is False
        assert hard_reset_called["v"] is False
        assert autorun.stop_event.called is False


def test_execute_aborted_does_not_set_trigger_cooldown():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")

        class _Evt:
            def set(self):
                return None

        class _Run:
            def __init__(self):
                self.pending_topics = []
                self.pending_exit_topics = []
                self.stop_event = _Evt()

            def _stop_ws_aggregator(self):
                return None

            def _cleanup_all_tasks(self):
                return None

        mgr._liquidate_positions = lambda _a: {
            "liquidated": 0,
            "maker_count": 0,
            "taker_count": 0,
            "errors": ["client init failed"],
            "aborted": True,
        }

        mgr.execute(_Run(), ["cond_a", "cond_b"])
        assert mgr._get_last_trigger_ts() == 0.0
        assert mgr._state.get("last_abort_ts") is not None


def test_execute_precheck_abort_keeps_runtime_intact():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")
        mgr._precheck_liquidation_ready = lambda: ("client init failed", None, None)

        class _Evt:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        touched = {"stop_ws": 0, "cleanup": 0}

        class _Run:
            def __init__(self):
                self.pending_topics = ["x"]
                self.pending_exit_topics = ["y"]
                self.stop_event = _Evt()

            def _stop_ws_aggregator(self):
                touched["stop_ws"] += 1

            def _cleanup_all_tasks(self):
                touched["cleanup"] += 1

        autorun = _Run()
        result = mgr.execute(autorun, ["cond_a", "cond_b"])

        assert result.get("aborted") is True
        assert touched["stop_ws"] == 0
        assert touched["cleanup"] == 0
        assert autorun.pending_topics == ["x"]
        assert autorun.pending_exit_topics == ["y"]
        assert autorun.stop_event.called is False


def test_execute_exception_requests_restart():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")
        mgr._precheck_liquidation_ready = lambda: (None, object(), {"A"})

        class _Evt:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        class _Run:
            def __init__(self):
                self.pending_topics = []
                self.pending_exit_topics = []
                self.stop_event = _Evt()

            def _stop_ws_aggregator(self):
                return None

            def _cleanup_all_tasks(self):
                return None

        mgr._liquidate_positions = lambda _a, **_kw: (_ for _ in ()).throw(RuntimeError("boom"))

        autorun = _Run()
        result = mgr.execute(autorun, ["cond_a", "cond_b"])
        assert any("boom" in e for e in result["errors"])
        assert autorun.stop_event.called is True


def test_execute_uses_prechecked_scope_without_reloading():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")

        class _Evt:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        class _Run:
            def __init__(self):
                self.pending_topics = []
                self.pending_exit_topics = []
                self.stop_event = _Evt()

            def _stop_ws_aggregator(self):
                return None

            def _cleanup_all_tasks(self):
                return None

        fake_client = object()
        mgr._precheck_liquidation_ready = lambda: (None, fake_client, {"A"})
        mgr._load_copytrade_token_scope = lambda: (_ for _ in ()).throw(RuntimeError("should not reload scope"))
        mgr._fetch_positions = lambda: [{"token_id": "A", "size": 10, "price": 0.5}]
        mgr._resolve_bid_ask = lambda _a, _t: (0.5, 0.5)
        called = []
        mgr._place_sell_ioc = lambda client, token_id, price, size: called.append((client, token_id)) or {}

        autorun = _Run()
        result = mgr.execute(autorun, ["cond_a", "cond_b"])
        assert result.get("aborted") is None
        assert result["liquidated"] == 1
        assert called and called[0][0] is fake_client
        assert autorun.stop_event.called is True


def test_should_trigger_fallback_startup_grace_when_metric_missing():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        cfg.total_liquidation["trigger"]["startup_grace_hours"] = 6
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        mgr._idle_since = time.time() - 3600
        mgr._last_trade_activity_ts = time.time()
        metrics = {
            "idle_since": mgr._idle_since,
            "last_trade_activity_ts": mgr._last_trade_activity_ts,
            "free_balance": 100.0,
        }
        ok, reasons = mgr.should_trigger(metrics)
        assert ok is False
        assert all("idle_slots" not in r for r in reasons)
