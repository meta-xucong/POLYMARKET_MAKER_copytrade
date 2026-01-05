from __future__ import annotations

import threading
import time
from typing import Iterable, Optional

from copytrade_v3_muti.ct_exec import (
    apply_actions,
    cancel_order,
    fetch_open_orders_norm,
    get_orderbook,
    reconcile_one,
)
from copytrade_v3_muti.ct_utils import safe_float

from .topic_selector import Topic


class PositionManager:
    def __init__(
        self,
        client,
        config: dict,
        maker_engine,
        *,
        max_concurrent_exits: int = 0,
        logger=None,
    ) -> None:
        self._client = client
        self._config = config
        self._maker_engine = maker_engine
        self._logger = logger
        self._exit_semaphore: Optional[threading.Semaphore] = None
        if max_concurrent_exits and max_concurrent_exits > 0:
            self._exit_semaphore = threading.Semaphore(int(max_concurrent_exits))

    def update_config(self, config: dict, *, max_concurrent_exits: Optional[int] = None) -> None:
        self._config = config
        if max_concurrent_exits is None:
            return
        if max_concurrent_exits and max_concurrent_exits > 0:
            self._exit_semaphore = threading.Semaphore(int(max_concurrent_exits))
        else:
            self._exit_semaphore = None

    def close_positions(self, topics: Iterable[Topic]) -> None:
        for topic in topics:
            token_ids = self._maker_engine.match_token_ids(topic)
            if not token_ids:
                self._log("warning", f"[position] 未找到匹配会话 token={topic.identifier}")
                continue
            for token_id in token_ids:
                position_size = self._maker_engine.open_positions().get(token_id, 0.0)
                if position_size <= 0:
                    self._log("info", f"[position] 无仓位，撤销挂单 token_id={token_id}")
                    self._cancel_open_orders(token_id)
                    self._maker_engine.update_open_size(token_id, 0.0)
                    continue
                if self._exit_semaphore and not self._exit_semaphore.acquire(blocking=False):
                    self._log("warning", f"[position] 清仓并发已满，跳过 token_id={token_id}")
                    continue
                thread = threading.Thread(
                    target=self._close_one,
                    args=(token_id, position_size),
                    name=f"close-{token_id}",
                    daemon=True,
                )
                thread.start()

    def _close_one(self, token_id: str, position_size: float) -> None:
        poll_sec = float(self._config.get("poll_interval_sec", 10))
        exit_poll_sec = float(self._config.get("exit_poll_interval_sec") or poll_sec)
        exit_timeout_sec = float(self._config.get("exit_timeout_sec") or 300.0)
        position_refresh_sec = float(self._config.get("exit_position_refresh_sec") or 10.0)
        cfg = self._build_exit_config()

        state = {"topic_state": {token_id: {"phase": "EXITING"}}}
        start_ts = time.time()
        last_position_fetch = 0.0
        current_size = float(position_size)
        try:
            while True:
                now = time.time()
                now_ts = int(now)
                if exit_timeout_sec > 0 and now - start_ts >= exit_timeout_sec:
                    self._log("warning", f"[position] 清仓超时 token_id={token_id}")
                    break

                if position_refresh_sec >= 0 and now - last_position_fetch >= position_refresh_sec:
                    fetched = self._fetch_position_size(token_id)
                    if fetched is not None:
                        current_size = fetched
                    last_position_fetch = now

                open_orders, orders_ok = self._fetch_open_orders(token_id)
                if not orders_ok:
                    time.sleep(exit_poll_sec)
                    continue
                if current_size <= 0 and not open_orders:
                    self._maker_engine.update_open_size(token_id, 0.0)
                    self._log("info", f"[position] 清仓完成 token_id={token_id}")
                    break

                orderbook = get_orderbook(self._client, token_id)
                if orderbook.get("best_bid") is None and orderbook.get("best_ask") is None:
                    self._log("warning", f"[position] orderbook 缺失，暂缓清仓 token_id={token_id}")
                    time.sleep(exit_poll_sec)
                    continue
                actions = reconcile_one(
                    token_id=token_id,
                    desired_shares=0.0,
                    my_shares=current_size,
                    orderbook=orderbook,
                    open_orders=open_orders,
                    now_ts=now_ts,
                    cfg=cfg,
                    state=state,
                )
                if actions:
                    open_orders = apply_actions(
                        self._client,
                        actions,
                        open_orders,
                        now_ts,
                        dry_run=False,
                        cfg=cfg,
                        state=state,
                    )
                time.sleep(exit_poll_sec)
        except Exception as exc:
            self._log("error", f"[position] 清仓失败 token_id={token_id}: {exc}")
        finally:
            if self._exit_semaphore:
                self._exit_semaphore.release()

    def _build_exit_config(self) -> dict:
        tick_size = float(self._config.get("exit_tick_size") or self._config.get("tick_size") or 0.0)
        taker_threshold = float(
            self._config.get("exit_taker_spread_threshold")
            or self._config.get("taker_spread_threshold")
            or 0.01
        )
        min_order_shares = float(
            self._config.get("exit_min_order_shares")
            or self._config.get("min_order_shares")
            or self._config.get("min_order_size")
            or 0.0
        )
        return {
            "tick_size": tick_size,
            "taker_enabled": bool(self._config.get("exit_taker_enabled", True)),
            "taker_spread_threshold": taker_threshold,
            "taker_order_type": self._config.get("exit_taker_order_type")
            or self._config.get("taker_order_type"),
            "exit_full_sell": True,
            "maker_only": bool(self._config.get("exit_maker_only", False)),
            "allow_short": bool(self._config.get("allow_short", False)),
            "order_size_mode": self._config.get("order_size_mode", "fixed_shares"),
            "slice_min": float(self._config.get("slice_min") or 0.0),
            "slice_max": float(self._config.get("slice_max") or 0.0),
            "min_order_usd": float(
                self._config.get("exit_min_order_usd") or self._config.get("min_order_usd") or 0.0
            ),
            "max_order_usd": float(self._config.get("max_order_usd") or 0.0),
            "min_order_shares": min_order_shares,
            "deadband_shares": float(
                self._config.get("exit_deadband_shares") or self._config.get("deadband_shares") or 0.0
            ),
            "enable_reprice": bool(self._config.get("exit_enable_reprice", True)),
            "reprice_ticks": int(self._config.get("exit_reprice_ticks") or self._config.get("reprice_ticks") or 1),
            "reprice_cooldown_sec": int(
                self._config.get("exit_reprice_cooldown_sec")
                or self._config.get("reprice_cooldown_sec")
                or 0
            ),
            "dedupe_place": bool(self._config.get("exit_dedupe_place", True)),
            "allow_partial": bool(self._config.get("exit_allow_partial", True)),
            "retry_on_insufficient_balance": bool(
                self._config.get("exit_retry_on_insufficient_balance", True)
            ),
            "retry_shrink_factor": float(self._config.get("exit_retry_shrink_factor") or 0.5),
            "place_fail_backoff_base_sec": float(
                self._config.get("place_fail_backoff_base_sec") or 2.0
            ),
            "place_fail_backoff_cap_sec": float(
                self._config.get("place_fail_backoff_cap_sec") or 60.0
            ),
        }

    def _fetch_open_orders(self, token_id: str) -> tuple[list[dict], bool]:
        orders, ok, err = fetch_open_orders_norm(self._client)
        if not ok:
            self._log("warning", f"[position] 获取挂单失败 token_id={token_id}: {err}")
            return [], False
        filtered = [order for order in orders if str(order.get("token_id")) == str(token_id)]
        return filtered, True

    def _cancel_open_orders(self, token_id: str) -> None:
        open_orders, ok = self._fetch_open_orders(token_id)
        if not ok:
            self._log("warning", f"[position] 无法获取挂单，跳过撤单 token_id={token_id}")
            return
        if not open_orders:
            return
        for order in open_orders:
            order_id = order.get("order_id") or order.get("id")
            if not order_id:
                continue
            try:
                cancel_order(self._client, str(order_id))
            except Exception as exc:
                self._log(
                    "warning",
                    f"[position] 撤单失败 token_id={token_id} order_id={order_id}: {exc}",
                )

    def _fetch_position_size(self, token_id: str) -> Optional[float]:
        positions = self._fetch_positions()
        if not positions:
            return None
        token_id_str = str(token_id)
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            pos_token = pos.get("token_id") or pos.get("tokenId") or pos.get("asset") or pos.get("token")
            if pos_token is None:
                continue
            if str(pos_token) != token_id_str:
                continue
            size = (
                pos.get("size")
                or pos.get("shares")
                or pos.get("position")
                or pos.get("amount")
            )
            parsed = safe_float(size)
            if parsed is not None:
                return parsed
        return None

    def _fetch_positions(self) -> list[dict]:
        method_candidates = (
            "list_positions",
            "get_positions",
            "fetch_positions",
            "get_user_positions",
            "list_user_positions",
        )
        for name in method_candidates:
            fn = getattr(self._client, name, None)
            if not callable(fn):
                continue
            try:
                resp = fn()
            except TypeError:
                continue
            except Exception as exc:
                self._log("warning", f"[position] 拉取仓位失败 client.{name}: {exc}")
                continue
            if isinstance(resp, list):
                return [item for item in resp if isinstance(item, dict)]
            if isinstance(resp, dict):
                for key in ("positions", "data", "results", "items", "list"):
                    val = resp.get(key)
                    if isinstance(val, list):
                        return [item for item in val if isinstance(item, dict)]
        return []

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            print(message)
            return
        log_fn = getattr(self._logger, level, None)
        if callable(log_fn):
            log_fn(message)
        else:
            self._logger.info(message)
