from __future__ import annotations

import json
import os
import re
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

    _COLLATERAL_DECIMALS = 6

    def __init__(self, cfg: Any, project_root: Path):
        self.cfg = LiquidationConfig.from_global_config(cfg)
        self.project_root = project_root
        self.state_path = cfg.data_dir / "total_liquidation_state.json"
        self._running = False

        self._idle_since: Optional[float] = None
        self._last_trade_activity_ts: float = time.time()
        self._last_fill_activity_ts: float = time.time()

        self._state = self._load_state()
        self._started_at_ts: float = time.time()

        self._cached_client: Optional[Any] = None
        self._next_client_retry_at: float = 0.0
        self._cached_free_balance: Optional[float] = None
        self._next_balance_probe_at: float = 0.0
        self._last_balance_probe_error: Optional[str] = None
        self._task_activity_markers: Dict[str, str] = {}

    _TRADE_ACTIVITY_HINTS = (
        "[maker][buy] 挂单",
        "[maker][sell] 挂单",
        "挂单成功",
        "撤单",
        "下单",
    )

    _FILL_ACTIVITY_HINTS = (
        "买入成交",
        "卖出成交",
        "成交",
    )

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

        latest_trade_activity, latest_fill_activity = self._collect_trade_activity_ts(autorun)
        if latest_trade_activity > 0:
            self._last_trade_activity_ts = max(self._last_trade_activity_ts, latest_trade_activity)
        if latest_fill_activity > 0:
            self._last_fill_activity_ts = max(self._last_fill_activity_ts, latest_fill_activity)

        free_balance = self._query_free_balance_usdc(autorun)
        startup_grace_sec = max(0.0, self.cfg.startup_grace_hours * 3600.0)
        in_startup_grace = (now - self._started_at_ts) < startup_grace_sec

        idle_minutes = ((now - float(self._idle_since)) / 60.0) if self._idle_since is not None else 0.0
        no_trade_minutes = (now - float(self._last_fill_activity_ts or now)) / 60.0
        bal_text = "NA" if free_balance is None else f"{float(free_balance):.4f}"
        print(
            "[GLB_LIQ][METRICS] "
            f"running={running}/{max_slots} idle_ratio={idle_ratio:.2f} "
            f"idle_minutes={idle_minutes:.1f} no_trade_minutes={no_trade_minutes:.1f} "
            f"free_balance={bal_text}"
        )
        if self._last_balance_probe_error:
            print(f"[GLB_LIQ][WARN] 余额查询失败，沿用缓存: {self._last_balance_probe_error}")

        return {
            "running": running,
            "max_slots": max_slots,
            "idle_ratio": idle_ratio,
            "idle_since": self._idle_since,
            "last_trade_activity_ts": self._last_trade_activity_ts,
            "last_fill_activity_ts": self._last_fill_activity_ts,
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

        in_startup_grace_flag = metrics.get("in_startup_grace")
        if in_startup_grace_flag is None:
            startup_grace_sec = max(0.0, self.cfg.startup_grace_hours * 3600.0)
            in_startup_grace = (now - self._started_at_ts) < startup_grace_sec
        else:
            in_startup_grace = bool(in_startup_grace_flag)

        idle_since = metrics.get("idle_since")
        if idle_since is not None and not in_startup_grace:
            idle_minutes = (now - float(idle_since)) / 60.0
            if idle_minutes >= self.cfg.idle_slot_duration_minutes:
                reasons.append(
                    f"idle_slots>={self.cfg.idle_slot_ratio_threshold:.2f} for {idle_minutes:.1f}m"
                )

        no_trade_minutes = (now - float(metrics.get("last_fill_activity_ts") or now)) / 60.0
        if no_trade_minutes >= self.cfg.no_trade_duration_minutes:
            reasons.append(f"no_trade_for={no_trade_minutes:.1f}m")

        free_balance = metrics.get("free_balance")
        if free_balance is not None and free_balance < self.cfg.min_free_balance:
            reasons.append(f"free_balance={free_balance:.4f}<min={self.cfg.min_free_balance:.4f}")

        return len(reasons) >= self.cfg.require_conditions, reasons

    def _collect_trade_activity_ts(self, autorun: Any) -> Tuple[float, float]:
        latest_trade = 0.0
        latest_fill = 0.0
        active_ids: set[str] = set()

        for topic_id, task in (getattr(autorun, "tasks", {}) or {}).items():
            if not task or not getattr(task, "is_running", lambda: False)():
                continue
            active_ids.add(str(topic_id))

            excerpt = str(getattr(task, "log_excerpt", "") or "")
            if not excerpt:
                continue

            last_line = (excerpt.strip().splitlines() or [""])[-1].strip()
            if not last_line:
                continue

            excerpt_ts = float(getattr(task, "last_log_excerpt_ts", 0.0) or 0.0)
            marker = f"{last_line}|{excerpt_ts:.3f}"
            if self._task_activity_markers.get(str(topic_id)) == marker:
                continue
            self._task_activity_markers[str(topic_id)] = marker

            normalized = last_line.lower()
            if any(hint in normalized for hint in self._TRADE_ACTIVITY_HINTS):
                latest_trade = max(latest_trade, time.time())
            if self._line_has_real_fill_activity(normalized):
                latest_fill = max(latest_fill, time.time())

        stale = [tid for tid in self._task_activity_markers.keys() if tid not in active_ids]
        for tid in stale:
            self._task_activity_markers.pop(tid, None)

        return latest_trade, latest_fill

    def _line_has_real_fill_activity(self, normalized_line: str) -> bool:
        """仅在明确出现正成交量时返回 True，避免 filled=0 / sold=0 误判。"""
        if not normalized_line:
            return False

        # 先处理显式数量字段：filled= / sold= 仅当数值 > 0 才算真实成交
        for key in ("filled", "sold"):
            m = re.search(rf"\b{key}\s*=\s*([0-9]+(?:\.[0-9]+)?)", normalized_line)
            if m is not None:
                try:
                    if float(m.group(1)) > 0:
                        return True
                except (TypeError, ValueError):
                    pass

        # 回退：中文成交语义（不含显式 0 数量时）
        return any(hint in normalized_line for hint in self._FILL_ACTIVITY_HINTS)

    def _precheck_liquidation_ready(self) -> Tuple[Optional[str], Optional[Any], Optional[set[str]]]:
        client = self._get_cached_client()
        if client is None:
            return "client init failed", None, None
        allowed_token_ids = self._load_copytrade_token_scope()
        if not allowed_token_ids:
            return "copytrade token scope is empty; skip liquidation for safety", None, None
        return None, client, allowed_token_ids

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
            precheck_error, prechecked_client, prechecked_token_scope = self._precheck_liquidation_ready()
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

            liquidation_stats = self._liquidate_positions(
                autorun,
                prechecked_client=prechecked_client,
                prechecked_token_scope=prechecked_token_scope,
            )
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
            try:
                # 若在停止运行态后抛异常，主动触发主循环重启以避免卡在半停机状态。
                autorun.stop_event.set()
            except Exception:
                pass
        finally:
            self._running = False

        return result

    @staticmethod
    def _normalize_collateral_balance(value: float, raw: Any) -> float:
        """
        CLOB balance-allowance 的 collateral balance 为 USDC 最小单位（6 decimals）。
        若原始值为纯整数（如 "53608824"），换算为 53.608824 USDC。
        """
        if isinstance(raw, bool):
            return value
        if isinstance(raw, int):
            return value / (10 ** TotalLiquidationManager._COLLATERAL_DECIMALS)
        if isinstance(raw, float):
            if raw.is_integer():
                return value / (10 ** TotalLiquidationManager._COLLATERAL_DECIMALS)
            return value
        if isinstance(raw, str):
            text = raw.strip()
            if text and text.lstrip("+-").isdigit():
                return value / (10 ** TotalLiquidationManager._COLLATERAL_DECIMALS)
        return value

    @staticmethod
    def _extract_balance_float(payload: Any, from_balance_key: bool = False) -> Optional[float]:
        """
        严格按 balance 语义字段提取余额，避免误取 allowance 等其他数值。
        只有命中余额语义键（balance/available）后的值才允许被解析为数值。
        """
        if isinstance(payload, (int, float)) and not isinstance(payload, bool):
            if not from_balance_key:
                return None
            return TotalLiquidationManager._normalize_collateral_balance(float(payload), payload)
        if isinstance(payload, str):
            if not from_balance_key:
                return None
            try:
                parsed = float(payload)
            except ValueError:
                return None
            return TotalLiquidationManager._normalize_collateral_balance(parsed, payload)
        if isinstance(payload, dict):
            for key in ("balance", "available", "availableBalance", "available_balance"):
                if key in payload:
                    parsed = TotalLiquidationManager._extract_balance_float(payload[key], True)
                    if parsed is not None:
                        return parsed

            # 已进入余额语义子树时，允许解析常见数值承载字段（如 amount/value）
            if from_balance_key:
                for key in ("amount", "value", "balance", "available", "availableBalance", "available_balance"):
                    if key in payload:
                        parsed = TotalLiquidationManager._extract_balance_float(payload[key], True)
                        if parsed is not None:
                            return parsed
                for v in payload.values():
                    if isinstance(v, (dict, list, tuple)):
                        parsed = TotalLiquidationManager._extract_balance_float(v, True)
                        if parsed is not None:
                            return parsed

            for v in payload.values():
                if isinstance(v, (dict, list, tuple)):
                    parsed = TotalLiquidationManager._extract_balance_float(v, False)
                    if parsed is not None:
                        return parsed
            return None
        if isinstance(payload, (list, tuple)):
            for item in payload:
                if from_balance_key:
                    parsed = TotalLiquidationManager._extract_balance_float(item, True)
                    if parsed is not None:
                        return parsed
                if isinstance(item, (dict, list, tuple)):
                    parsed = TotalLiquidationManager._extract_balance_float(item, False)
                    if parsed is not None:
                        return parsed
        return None

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
        self._last_balance_probe_error = None

        client = self._get_cached_client()
        if client is None:
            self._last_balance_probe_error = "client init failed"
            return self._cached_free_balance

        # 严格使用官方 py-clob-client 方法：get_balance_allowance(BalanceAllowanceParams)
        get_balance_allowance = getattr(client, "get_balance_allowance", None)
        if not callable(get_balance_allowance):
            self._last_balance_probe_error = "client.get_balance_allowance 不可调用"
            return self._cached_free_balance

        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
        except Exception as exc:
            self._last_balance_probe_error = f"py_clob_client 类型导入失败: {exc}"
            return self._cached_free_balance

        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            token_id=None,
            signature_type=-1,
        )

        try:
            resp = get_balance_allowance(params)
            parsed = self._extract_balance_float(resp)
            if parsed is not None:
                self._cached_free_balance = parsed
                return self._cached_free_balance
            self._last_balance_probe_error = f"响应中未找到 balance 字段: {resp}"
        except Exception as exc:
            self._last_balance_probe_error = str(exc)

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
    def _is_fak_no_match_error(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return "no orders found to match" in text and "fak" in text

    @staticmethod
    def _build_sell_price_ladder(price: float) -> List[float]:
        base = max(float(price or 0.0), 0.01)
        ladder = [base, max(0.01, base * 0.997), max(0.01, base * 0.992), max(0.01, base * 0.985), 0.01]
        dedup: List[float] = []
        for px in ladder:
            px = round(float(px), 4)
            if dedup and abs(dedup[-1] - px) < 1e-9:
                continue
            dedup.append(px)
        return dedup

    def _place_sell_ioc(self, client: Any, token_id: str, price: float, size: float) -> Dict[str, Any]:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        eff_size = max(float(size), 0.0)
        order_type = getattr(OrderType, "FAK", None) or getattr(OrderType, "IOC", OrderType.FOK)
        last_exc: Optional[Exception] = None

        for idx, eff_price in enumerate(self._build_sell_price_ladder(price), start=1):
            order = OrderArgs(token_id=str(token_id), side=SELL, price=max(float(eff_price), 0.01), size=eff_size)
            signed = client.create_order(order)
            try:
                resp = client.post_order(signed, order_type)
                if idx > 1:
                    print(
                        f"[GLB_LIQ][IOC] token={token_id} 阶梯价格 level={idx} price={eff_price:.4f} 下单成功"
                    )
                return resp
            except Exception as exc:
                last_exc = exc
                if self._is_fak_no_match_error(exc):
                    print(
                        f"[GLB_LIQ][IOC] token={token_id} level={idx} price={eff_price:.4f} "
                        "无可匹配买单，继续尝试更低价"
                    )
                    continue
                raise

        if last_exc is not None:
            raise last_exc
        return {}

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

    def _liquidate_positions(
        self,
        autorun: Any,
        *,
        prechecked_client: Optional[Any] = None,
        prechecked_token_scope: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        from maker_execution import maker_sell_follow_ask_with_floor_wait

        client = prechecked_client if prechecked_client is not None else self._get_cached_client()
        if client is None:
            return {"liquidated": 0, "maker_count": 0, "taker_count": 0, "errors": ["client init failed"], "aborted": True}

        positions = self._fetch_positions()
        allowed_token_ids = prechecked_token_scope if prechecked_token_scope is not None else self._load_copytrade_token_scope()
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
