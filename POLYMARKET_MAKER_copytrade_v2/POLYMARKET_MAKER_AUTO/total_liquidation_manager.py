from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import urllib.parse
import urllib.request


@dataclass
class LiquidationConfig:
    enabled: bool = False
    min_interval_hours: float = 72.0
    idle_slot_ratio_threshold: float = 0.5
    idle_slot_duration_minutes: float = 120.0
    no_trade_duration_minutes: float = 180.0
    min_free_balance: float = 20.0
    require_conditions: int = 2
    position_value_threshold: float = 3.0
    spread_threshold: float = 0.01
    maker_timeout_minutes: float = 20.0
    taker_slippage_bps: float = 30.0
    hard_reset_enabled: bool = True
    remove_logs: bool = True
    remove_json_state: bool = True

    @classmethod
    def from_global_config(cls, cfg: Any) -> "LiquidationConfig":
        raw = getattr(cfg, "total_liquidation", None) or {}
        trigger = raw.get("trigger") or {}
        liquidation = raw.get("liquidation") or {}
        reset = raw.get("reset") or {}
        return cls(
            enabled=bool(raw.get("enable_total_liquidation", False)),
            min_interval_hours=float(raw.get("min_interval_hours", 72.0)),
            idle_slot_ratio_threshold=float(trigger.get("idle_slot_ratio_threshold", 0.5)),
            idle_slot_duration_minutes=float(trigger.get("idle_slot_duration_minutes", 120.0)),
            no_trade_duration_minutes=float(trigger.get("no_trade_duration_minutes", 180.0)),
            min_free_balance=float(trigger.get("min_free_balance", 20.0)),
            require_conditions=max(1, int(trigger.get("require_conditions", 2))),
            position_value_threshold=float(liquidation.get("position_value_threshold", 3.0)),
            spread_threshold=float(liquidation.get("spread_threshold", 0.01)),
            maker_timeout_minutes=float(liquidation.get("maker_timeout_minutes", 20.0)),
            taker_slippage_bps=float(liquidation.get("taker_slippage_bps", 30.0)),
            hard_reset_enabled=bool(reset.get("hard_reset_enabled", True)),
            remove_logs=bool(reset.get("remove_logs", True)),
            remove_json_state=bool(reset.get("remove_json_state", True)),
        )


class TotalLiquidationManager:
    """全局清仓管理器：监控活跃度 -> 触发清仓 -> 硬重置。"""

    def __init__(self, cfg: Any, project_root: Path):
        self.cfg = LiquidationConfig.from_global_config(cfg)
        self.project_root = project_root
        self.state_path = cfg.data_dir / "total_liquidation_state.json"
        self._running = False

        self._idle_since: Optional[float] = None
        self._last_trade_activity_ts: float = time.time()

        self._state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            with self.state_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _save_state(self, payload: Dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        self._state = dict(payload)

    def _get_last_trigger_ts(self) -> float:
        value = self._state.get("last_trigger_ts")
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def update_metrics(self, autorun: Any) -> Dict[str, Any]:
        now = time.time()
        running = sum(1 for t in autorun.tasks.values() if t.is_running())
        max_slots = max(1, int(getattr(autorun.config, "max_concurrent_tasks", 1)))
        idle_ratio = max(0.0, min(1.0, 1.0 - (running / max_slots)))

        if idle_ratio >= self.cfg.idle_slot_ratio_threshold:
            if self._idle_since is None:
                self._idle_since = now
        else:
            self._idle_since = None

        latest_ws_update = 0.0
        with autorun._ws_cache_lock:
            for data in autorun._ws_cache.values():
                try:
                    ts = float(data.get("updated_at") or 0.0)
                except (TypeError, ValueError):
                    ts = 0.0
                latest_ws_update = max(latest_ws_update, ts)

        if latest_ws_update > 0:
            self._last_trade_activity_ts = max(self._last_trade_activity_ts, latest_ws_update)

        free_balance = self._query_free_balance_usdc(autorun)

        return {
            "running": running,
            "max_slots": max_slots,
            "idle_ratio": idle_ratio,
            "idle_since": self._idle_since,
            "last_trade_activity_ts": self._last_trade_activity_ts,
            "free_balance": free_balance,
        }

    def should_trigger(self, metrics: Dict[str, Any]) -> Tuple[bool, List[str]]:
        if not self.cfg.enabled or self._running:
            return False, []

        now = time.time()
        min_interval_sec = max(1.0, self.cfg.min_interval_hours * 3600.0)
        if now - self._get_last_trigger_ts() < min_interval_sec:
            return False, []

        reasons: List[str] = []

        idle_since = metrics.get("idle_since")
        if idle_since is not None:
            idle_minutes = (now - float(idle_since)) / 60.0
            if idle_minutes >= self.cfg.idle_slot_duration_minutes:
                reasons.append(f"idle_slots>={self.cfg.idle_slot_ratio_threshold:.2f} for {idle_minutes:.1f}m")

        no_trade_minutes = (now - float(metrics.get("last_trade_activity_ts") or now)) / 60.0
        if no_trade_minutes >= self.cfg.no_trade_duration_minutes:
            reasons.append(f"no_trade_for={no_trade_minutes:.1f}m")

        free_balance = metrics.get("free_balance")
        if free_balance is not None and free_balance < self.cfg.min_free_balance:
            reasons.append(f"free_balance={free_balance:.4f}<min={self.cfg.min_free_balance:.4f}")

        return len(reasons) >= self.cfg.require_conditions, reasons

    def execute(self, autorun: Any, reasons: List[str]) -> Dict[str, Any]:
        self._running = True
        start = time.time()
        print(f"[GLB_LIQ] 开始总清仓流程, reasons={reasons}")

        result: Dict[str, Any] = {
            "trigger_reasons": reasons,
            "liquidated": 0,
            "maker_count": 0,
            "taker_count": 0,
            "errors": [],
            "hard_reset": False,
        }

        try:
            # 1) 停止当前运行任务/订阅
            autorun._stop_ws_aggregator()
            autorun._cleanup_all_tasks()
            autorun.pending_topics.clear()
            autorun.pending_exit_topics.clear()

            # 2) 执行清仓
            liquidation_stats = self._liquidate_positions(autorun)
            result.update(liquidation_stats)

            # 3) 一刀切硬重置
            if self.cfg.hard_reset_enabled:
                self._hard_reset_files(autorun)
                result["hard_reset"] = True

            # 4) 记录状态
            now = time.time()
            state = {
                "last_trigger_ts": now,
                "last_trigger_reason": reasons,
                "last_result": result,
                "last_duration_sec": now - start,
            }
            self._save_state(state)

            # 5) 重启（由外层守护拉起）
            print("[GLB_LIQ] 总清仓完成，准备重启 autorun")
            autorun.stop_event.set()
        except Exception as exc:
            result["errors"].append(str(exc))
            print(f"[GLB_LIQ][ERROR] 总清仓流程失败: {exc}")
        finally:
            self._running = False

        return result

    def _query_free_balance_usdc(self, autorun: Any) -> Optional[float]:
        """优先读取环境变量覆盖，避免强依赖交易所余额接口。"""
        override = os.getenv("POLY_FREE_BALANCE_OVERRIDE")
        if override is not None:
            try:
                return float(override)
            except ValueError:
                return None
        # 暂不强依赖外部余额接口，返回 None 表示跳过该触发条件
        return None

    def _resolve_wallet(self) -> Optional[str]:
        for key in ("POLY_DATA_ADDRESS", "POLY_FUNDER", "POLY_WALLET", "POLY_ADDRESS"):
            cand = os.getenv(key)
            if cand and str(cand).strip():
                return str(cand).strip()
        return None

    def _fetch_positions(self) -> List[Dict[str, Any]]:
        address = self._resolve_wallet()
        if not address:
            return []

        url = os.getenv("POLY_DATA_API_ROOT", "https://data-api.polymarket.com").rstrip("/") + "/positions"
        params = {"user": address, "sizeThreshold": 0, "limit": 500, "offset": 0}
        out: List[Dict[str, Any]] = []
        try:
            while True:
                query = urllib.parse.urlencode(params)
                with urllib.request.urlopen(f"{url}?{query}", timeout=20) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                items = payload if isinstance(payload, list) else payload.get("data")
                if not isinstance(items, list):
                    break
                if not items:
                    break
                out.extend([x for x in items if isinstance(x, dict)])
                if len(items) < int(params["limit"]):
                    break
                params["offset"] = int(params["offset"]) + len(items)
        except Exception as exc:
            print(f"[GLB_LIQ][WARN] 拉取持仓失败: {exc}")
        return out

    @staticmethod
    def _extract_token(entry: Dict[str, Any]) -> Optional[str]:
        for key in ("token_id", "tokenId", "asset", "asset_id"):
            val = entry.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        return None

    @staticmethod
    def _extract_size(entry: Dict[str, Any]) -> float:
        for key in ("size", "position", "position_size", "balance", "amount", "shares"):
            val = entry.get(key)
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _extract_price(entry: Dict[str, Any]) -> float:
        for key in ("current_price", "price", "avgPrice", "average_price", "entry_price"):
            val = entry.get(key)
            try:
                px = float(val)
                if px > 0:
                    return px
            except (TypeError, ValueError):
                continue
        return 0.0

    def _load_client(self) -> Any:
        from Volatility_arbitrage_run import _get_client

        return _get_client()

    @staticmethod
    def _place_sell_ioc(client: Any, token_id: str, price: float, size: float) -> Dict[str, Any]:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        if price <= 0:
            price = 0.01
        order = OrderArgs(token_id=str(token_id), side=SELL, price=float(price), size=float(size))
        signed = client.create_order(order)
        order_type = getattr(OrderType, "FAK", None) or getattr(OrderType, "IOC", OrderType.FOK)
        return client.post_order(signed, order_type)

    def _liquidate_positions(self, autorun: Any) -> Dict[str, Any]:
        from maker_execution import maker_sell_follow_ask_with_floor_wait

        client = self._load_client()
        positions = self._fetch_positions()

        maker_count = 0
        taker_count = 0
        liquidated = 0
        errors: List[str] = []

        for entry in positions:
            token_id = self._extract_token(entry)
            if not token_id:
                continue
            size = self._extract_size(entry)
            if size <= 0:
                continue
            value = size * max(self._extract_price(entry), 0.0)
            if value < self.cfg.position_value_threshold:
                continue

            with autorun._ws_cache_lock:
                cached = dict(autorun._ws_cache.get(token_id) or {})
            bid = float(cached.get("best_bid") or 0.0)
            ask = float(cached.get("best_ask") or 0.0)
            spread = (ask - bid) if (ask > 0 and bid > 0 and ask >= bid) else 0.0

            try:
                if spread > self.cfg.spread_threshold:
                    maker_count += 1

                    def _best_ask_fn() -> Optional[float]:
                        with autorun._ws_cache_lock:
                            d = dict(autorun._ws_cache.get(token_id) or {})
                        v = d.get("best_ask")
                        try:
                            f = float(v)
                            return f if f > 0 else None
                        except (TypeError, ValueError):
                            return None

                    floor_x = max(0.01, bid * 0.98 if bid > 0 else 0.01)
                    maker_sell_follow_ask_with_floor_wait(
                        client=client,
                        token_id=token_id,
                        position_size=size,
                        floor_X=floor_x,
                        poll_sec=max(1.0, float(getattr(autorun.config, "maker_poll_sec", 10.0))),
                        best_ask_fn=_best_ask_fn,
                        inactive_timeout_sec=max(60.0, self.cfg.maker_timeout_minutes * 60.0),
                        sell_mode="aggressive",
                    )
                else:
                    taker_count += 1
                    exit_price = max(0.01, bid if bid > 0 else ask if ask > 0 else 0.01)
                    self._place_sell_ioc(client, token_id, exit_price, size)
                liquidated += 1
            except Exception as exc:
                errors.append(f"token={token_id}: {exc}")

        return {
            "liquidated": liquidated,
            "maker_count": maker_count,
            "taker_count": taker_count,
            "errors": errors,
        }

    def _hard_reset_files(self, autorun: Any) -> None:
        print("[GLB_LIQ] 执行一刀切重置: logs/json")

        copytrade_dir = self.project_root.parent / "copytrade"
        targets: List[Path] = [autorun.config.data_dir, autorun.config.log_dir, copytrade_dir]

        for root in targets:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*"), reverse=True):
                try:
                    if path.is_file():
                        if self.cfg.remove_logs and path.suffix.lower() in {".log", ".tmp"}:
                            path.unlink(missing_ok=True)
                            continue
                        if self.cfg.remove_json_state and path.suffix.lower() == ".json":
                            path.unlink(missing_ok=True)
                            continue
                    elif path.is_dir():
                        if path == root:
                            continue
                        try:
                            if not any(path.iterdir()):
                                path.rmdir()
                        except OSError:
                            pass
                except OSError:
                    continue

        # 重建必要目录
        autorun.config.log_dir.mkdir(parents=True, exist_ok=True)
        autorun.config.data_dir.mkdir(parents=True, exist_ok=True)
        copytrade_dir.mkdir(parents=True, exist_ok=True)
