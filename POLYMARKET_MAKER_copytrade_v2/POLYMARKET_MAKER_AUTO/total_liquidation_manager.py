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
    startup_grace_hours: float = 6.0
    no_trade_duration_minutes: float = 180.0
    min_free_balance: float = 20.0
    balance_poll_interval_sec: float = 120.0
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
            startup_grace_hours=max(0.0, float(trigger.get("startup_grace_hours", 6.0))),
            no_trade_duration_minutes=float(trigger.get("no_trade_duration_minutes", 180.0)),
            min_free_balance=float(trigger.get("min_free_balance", 20.0)),
            balance_poll_interval_sec=max(5.0, float(trigger.get("balance_poll_interval_sec", 120.0))),
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
        self._started_at_ts: float = time.time()

        self._cached_client: Optional[Any] = None
        self._next_client_retry_at: float = 0.0
        self._cached_free_balance: Optional[float] = None
        self._next_balance_probe_at: float = 0.0

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
        startup_grace_sec = max(0.0, self.cfg.startup_grace_hours * 3600.0)
        in_startup_grace = (now - self._started_at_ts) < startup_grace_sec

        return {
            "running": running,
            "max_slots": max_slots,
            "idle_ratio": idle_ratio,
            "idle_since": self._idle_since,
            "last_trade_activity_ts": self._last_trade_activity_ts,
            "free_balance": free_balance,
            "in_startup_grace": in_startup_grace,
        }

    def should_trigger(self, metrics: Dict[str, Any]) -> Tuple[bool, List[str]]:
        if not self.cfg.enabled or self._running:
            return False, []

        now = time.time()
        min_interval_sec = max(1.0, self.cfg.min_interval_hours * 3600.0)
        if now - self._get_last_trigger_ts() < min_interval_sec:
            return False, []

        reasons: List[str] = []

        in_startup_grace = bool(metrics.get("in_startup_grace", False))

        idle_since = metrics.get("idle_since")
        if idle_since is not None and not in_startup_grace:
            idle_minutes = (now - float(idle_since)) / 60.0
            if idle_minutes >= self.cfg.idle_slot_duration_minutes:
                reasons.append(
                    f"idle_slots>={self.cfg.idle_slot_ratio_threshold:.2f} for {idle_minutes:.1f}m"
                )

        no_trade_minutes = (now - float(metrics.get("last_trade_activity_ts") or now)) / 60.0
        if no_trade_minutes >= self.cfg.no_trade_duration_minutes:
            reasons.append(f"no_trade_for={no_trade_minutes:.1f}m")

        free_balance = metrics.get("free_balance")
        if free_balance is not None and free_balance < self.cfg.min_free_balance:
            reasons.append(f"free_balance={free_balance:.4f}<min={self.cfg.min_free_balance:.4f}")

        return len(reasons) >= self.cfg.require_conditions, reasons

    def _precheck_liquidation_ready(self) -> Optional[str]:
        if self._get_cached_client() is None:
            return "client init failed"
        if not self._load_copytrade_token_scope():
            return "copytrade token scope is empty; skip liquidation for safety"
        return None

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
            precheck_error = self._precheck_liquidation_ready()
            if precheck_error:
                result.update({"errors": [precheck_error], "aborted": True})
                now = time.time()
                self._save_state(
                    {
                        "last_abort_ts": now,
                        "last_abort_reason": reasons,
                        "last_result": result,
                        "last_duration_sec": now - start,
                    }
                )
                print(f"[GLB_LIQ][WARN] 预检失败，跳过总清仓: {precheck_error}")
                return result

            autorun._stop_ws_aggregator()
            autorun._cleanup_all_tasks()
            autorun.pending_topics.clear()
            autorun.pending_exit_topics.clear()

            liquidation_stats = self._liquidate_positions(autorun)
            result.update(liquidation_stats)

            now = time.time()
            aborted = bool(liquidation_stats.get("aborted", False))
            if aborted:
                self._save_state(
                    {
                        "last_abort_ts": now,
                        "last_abort_reason": reasons,
                        "last_result": result,
                        "last_duration_sec": now - start,
                    }
                )
                print("[GLB_LIQ][WARN] 本次总清仓已中止，跳过硬重置与重启")
                return result

            state = {
                "last_trigger_ts": now,
                "last_trigger_reason": reasons,
                "last_result": result,
                "last_duration_sec": now - start,
            }
            self._save_state(state)

            if self.cfg.hard_reset_enabled:
                self._hard_reset_files(autorun)
                result["hard_reset"] = True

            print("[GLB_LIQ] 总清仓完成，准备重启 autorun")
            autorun.stop_event.set()
        except Exception as exc:
            result["errors"].append(str(exc))
            print(f"[GLB_LIQ][ERROR] 总清仓流程失败: {exc}")
        finally:
            self._running = False

        return result

    @staticmethod
    def _extract_first_float(payload: Any) -> Optional[float]:
        if isinstance(payload, (int, float)) and not isinstance(payload, bool):
            return float(payload)
        if isinstance(payload, str):
            try:
                return float(payload)
            except ValueError:
                return None
        if isinstance(payload, dict):
            for key in ("available", "availableBalance", "available_balance", "balance", "amount", "value"):
                if key in payload:
                    parsed = TotalLiquidationManager._extract_first_float(payload[key])
                    if parsed is not None:
                        return parsed
            for v in payload.values():
                parsed = TotalLiquidationManager._extract_first_float(v)
                if parsed is not None:
                    return parsed
        if isinstance(payload, (list, tuple)):
            for item in payload:
                parsed = TotalLiquidationManager._extract_first_float(item)
                if parsed is not None:
                    return parsed
        return None

    def _get_cached_client(self) -> Optional[Any]:
        now = time.time()
        if self._cached_client is not None:
            return self._cached_client
        if now < self._next_client_retry_at:
            return None
        try:
            self._cached_client = self._load_client()
            return self._cached_client
        except Exception:
            self._next_client_retry_at = now + 60.0
            return None

    def _query_free_balance_usdc(self, autorun: Any) -> Optional[float]:
        if not self.cfg.enabled:
            return None
        override = os.getenv("POLY_FREE_BALANCE_OVERRIDE")
        if override is not None:
            try:
                return float(override)
            except ValueError:
                return None

        now = time.time()
        if now < self._next_balance_probe_at:
            return self._cached_free_balance

        self._next_balance_probe_at = now + self.cfg.balance_poll_interval_sec

        client = self._get_cached_client()
        if client is None:
            return self._cached_free_balance

        # 严格使用官方 py-clob-client 方法：get_balance_allowance(BalanceAllowanceParams)
        get_balance_allowance = getattr(client, "get_balance_allowance", None)
        if not callable(get_balance_allowance):
            return self._cached_free_balance

        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                token_id=None,
                signature_type=-1,
            )
            resp = get_balance_allowance(params)
            parsed = self._extract_first_float(resp)
            if parsed is not None:
                self._cached_free_balance = parsed
        except Exception:
            # 在本地缺少 py_clob_client 类型定义时，仍尝试调用官方同名方法。
            try:
                resp = get_balance_allowance(None)
                parsed = self._extract_first_float(resp)
                if parsed is not None:
                    self._cached_free_balance = parsed
            except Exception:
                pass

        return self._cached_free_balance

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
                if not isinstance(items, list) or not items:
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
        for key in ("current_price", "price", "avgPrice", "average_price", "entry_price", "mark_price"):
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

        eff_price = max(float(price), 0.01)
        eff_size = max(float(size), 0.0)
        order = OrderArgs(token_id=str(token_id), side=SELL, price=eff_price, size=eff_size)
        signed = client.create_order(order)
        order_type = getattr(OrderType, "FAK", None) or getattr(OrderType, "IOC", OrderType.FOK)
        return client.post_order(signed, order_type)

    def _estimate_value(self, size: float, pos_price: float, bid: float, ask: float) -> float:
        if pos_price > 0:
            return size * pos_price
        if bid > 0 and ask > 0 and ask >= bid:
            return size * ((bid + ask) / 2.0)
        if bid > 0:
            return size * bid
        if ask > 0:
            return size * ask
        return 0.0

    def _resolve_bid_ask(self, autorun: Any, token_id: str) -> Tuple[float, float]:
        with autorun._ws_cache_lock:
            cached = dict(autorun._ws_cache.get(token_id) or {})
        try:
            bid = float(cached.get("best_bid") or 0.0)
        except (TypeError, ValueError):
            bid = 0.0
        try:
            ask = float(cached.get("best_ask") or 0.0)
        except (TypeError, ValueError):
            ask = 0.0
        return bid, ask

    def _compute_taker_price(self, bid: float, ask: float) -> float:
        base = bid if bid > 0 else ask if ask > 0 else 0.01
        bps = max(0.0, float(self.cfg.taker_slippage_bps))
        return max(0.01, base * (1.0 - bps / 10000.0))

    def _fetch_single_position_size(self, token_id: str) -> float:
        for entry in self._fetch_positions():
            if self._extract_token(entry) == str(token_id):
                return max(self._extract_size(entry), 0.0)
        return 0.0

    def _load_copytrade_token_scope(self) -> set[str]:
        copytrade_dir = self.project_root.parent / "copytrade"
        tokens_path = copytrade_dir / "tokens_from_copytrade.json"
        signals_path = copytrade_dir / "copytrade_sell_signals.json"
        token_ids: set[str] = set()

        for path, key in ((tokens_path, "tokens"), (signals_path, "sell_tokens")):
            if not path.exists():
                continue
            try:
                with path.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
                rows = payload.get(key) if isinstance(payload, dict) else None
                if isinstance(rows, list):
                    for item in rows:
                        if not isinstance(item, dict):
                            continue
                        tid = item.get("token_id") or item.get("tokenId")
                        if tid is not None and str(tid).strip():
                            token_ids.add(str(tid).strip())
            except Exception:
                continue
        return token_ids

    def _liquidate_positions(self, autorun: Any) -> Dict[str, Any]:
        from maker_execution import maker_sell_follow_ask_with_floor_wait

        client = self._get_cached_client()
        if client is None:
            return {"liquidated": 0, "maker_count": 0, "taker_count": 0, "errors": ["client init failed"], "aborted": True}

        positions = self._fetch_positions()
        allowed_token_ids = self._load_copytrade_token_scope()
        if not allowed_token_ids:
            return {
                "liquidated": 0,
                "maker_count": 0,
                "taker_count": 0,
                "errors": ["copytrade token scope is empty; skip liquidation for safety"],
                "aborted": True,
            }

        maker_count = 0
        taker_count = 0
        liquidated = 0
        errors: List[str] = []

        for entry in positions:
            token_id = self._extract_token(entry)
            if not token_id:
                continue
            if allowed_token_ids and token_id not in allowed_token_ids:
                continue

            size = self._extract_size(entry)
            if size <= 0:
                continue

            bid, ask = self._resolve_bid_ask(autorun, token_id)
            pos_price = self._extract_price(entry)
            value = self._estimate_value(size=size, pos_price=pos_price, bid=bid, ask=ask)
            if value < self.cfg.position_value_threshold:
                continue

            try:
                spread = (ask - bid) if (ask > 0 and bid > 0 and ask >= bid) else 0.0

                if spread > self.cfg.spread_threshold:
                    maker_count += 1

                    def _best_ask_fn() -> Optional[float]:
                        _bid, _ask = self._resolve_bid_ask(autorun, token_id)
                        return _ask if _ask > 0 else None

                    floor_x = self._compute_taker_price(bid=bid, ask=ask)
                    maker_resp = maker_sell_follow_ask_with_floor_wait(
                        client=client,
                        token_id=token_id,
                        position_size=size,
                        floor_X=floor_x,
                        poll_sec=max(1.0, float(getattr(autorun.config, "maker_poll_sec", 10.0))),
                        best_ask_fn=_best_ask_fn,
                        inactive_timeout_sec=max(60.0, self.cfg.maker_timeout_minutes * 60.0),
                        sell_mode="aggressive",
                    )

                    maker_status = str((maker_resp or {}).get("status") or "").upper()
                    if maker_status in {"ABANDONED", "FAILED", "PRICE_TIMEOUT", "STOPPED", "PENDING"}:
                        remain = self._fetch_single_position_size(token_id)
                        if remain > 0:
                            taker_count += 1
                            bid2, ask2 = self._resolve_bid_ask(autorun, token_id)
                            self._place_sell_ioc(
                                client,
                                token_id,
                                self._compute_taker_price(bid=bid2, ask=ask2),
                                remain,
                            )
                else:
                    taker_count += 1
                    self._place_sell_ioc(client, token_id, self._compute_taker_price(bid=bid, ask=ask), size)

                liquidated += 1
            except Exception as exc:
                errors.append(f"token={token_id}: {exc}")

        return {
            "liquidated": liquidated,
            "maker_count": maker_count,
            "taker_count": taker_count,
            "errors": errors,
        }

    @staticmethod
    def _safe_write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _reset_copytrade_state_files(self) -> None:
        copytrade_dir = self.project_root.parent / "copytrade"
        copytrade_dir.mkdir(parents=True, exist_ok=True)

        targets = {
            "tokens_from_copytrade.json": {"updated_at": "", "tokens": []},
            "copytrade_sell_signals.json": {"updated_at": "", "sell_tokens": []},
            "copytrade_state.json": {"targets": {}},
            "sell_tokens_from_copytrade.json": {"updated_at": "", "sell_tokens": []},
        }
        for filename, payload in targets.items():
            self._safe_write_json(copytrade_dir / filename, payload)

    def _hard_reset_files(self, autorun: Any) -> None:
        print("[GLB_LIQ] 执行一刀切重置: logs/json")

        if self.cfg.remove_logs and autorun.config.log_dir.exists():
            for path in sorted(autorun.config.log_dir.rglob("*"), reverse=True):
                try:
                    if path.is_file() and path.suffix.lower() in {".log", ".tmp"}:
                        path.unlink(missing_ok=True)
                    elif path.is_dir() and path != autorun.config.log_dir and not any(path.iterdir()):
                        path.rmdir()
                except OSError:
                    continue

        if self.cfg.remove_json_state and autorun.config.data_dir.exists():
            keep_names = {"total_liquidation_state.json"}
            for path in autorun.config.data_dir.glob("*.json"):
                if path.name in keep_names:
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue

        if self.cfg.remove_json_state:
            self._reset_copytrade_state_files()

        autorun.config.log_dir.mkdir(parents=True, exist_ok=True)
        autorun.config.data_dir.mkdir(parents=True, exist_ok=True)
