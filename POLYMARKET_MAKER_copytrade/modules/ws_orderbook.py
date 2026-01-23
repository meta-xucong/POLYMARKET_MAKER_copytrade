from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional, Set, Tuple


class WsOrderbookCache:
    def __init__(
        self,
        *,
        ws_watch: Callable[..., None],
        logger: Optional[Any] = None,
        stale_sec: float = 30.0,
        max_stale_sec: float = 300.0,
    ) -> None:
        self._ws_watch = ws_watch
        self._logger = logger
        self._stale_sec = stale_sec
        self._max_stale_sec = max_stale_sec
        self._lock = threading.Lock()
        self._latest: Dict[str, Dict[str, Any]] = {}
        self._tokens: Set[str] = set()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        # 统计信息
        self._stats_lock = threading.Lock()
        self._update_count: Dict[str, int] = {}  # token -> 更新次数
        self._last_stats_time = time.time()
        self._last_stats_report = time.time()

    def update_tokens(self, token_ids: Iterable[str]) -> None:
        normalized = {str(tid) for tid in token_ids if tid}
        with self._lock:
            if normalized == self._tokens:
                return
            self._tokens = normalized
        self._restart()

    def get_best(self, token_id: str, allow_stale: bool = False) -> Tuple[Optional[float], Optional[float]]:
        now = time.time()
        with self._lock:
            entry = self._latest.get(str(token_id))
            if not entry:
                self._log("debug", f"[WS][GET_BEST] token={token_id[:16]}... 无快照数据")
                return None, None
            ts = float(entry.get("ts") or 0.0)
            if not ts:
                self._log("debug", f"[WS][GET_BEST] token={token_id[:16]}... 快照无时间戳")
                return None, None

            age = now - ts
            bid = entry.get("best_bid")
            ask = entry.get("best_ask")

            # 分级判断：正常 -> stale -> 过期
            if self._stale_sec > 0 and age > self._stale_sec:
                # 超过正常阈值，但在最大阈值内
                if allow_stale and self._max_stale_sec > 0 and age <= self._max_stale_sec:
                    self._log(
                        "debug",
                        f"[WS][GET_BEST] token={token_id[:16]}... 使用陈旧快照: age={age:.1f}s (阈值={self._stale_sec:.1f}s, 最大={self._max_stale_sec:.1f}s), bid={bid}, ask={ask}"
                    )
                    return bid, ask
                # 超过最大阈值，拒绝使用
                self._log(
                    "warning",
                    f"[WS][GET_BEST] token={token_id[:16]}... 快照过期: age={age:.1f}s > 阈值={self._stale_sec:.1f}s (max={self._max_stale_sec:.1f}s), bid={bid}, ask={ask}"
                )
                return None, None

            # 快照新鲜，正常返回
            self._log(
                "debug",
                f"[WS][GET_BEST] token={token_id[:16]}... 快照新鲜: age={age:.1f}s, bid={bid}, ask={ask}"
            )
            return bid, ask

    def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    def _restart(self) -> None:
        self.stop()
        with self._lock:
            tokens = list(self._tokens)
        if not tokens:
            return
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._run_watch,
            kwargs={"token_ids": tokens, "stop_event": stop_event},
            daemon=True,
        )
        self._stop_event = stop_event
        self._thread = thread
        thread.start()

    def _run_watch(self, *, token_ids: list[str], stop_event: threading.Event) -> None:
        def _on_event(ev: Dict[str, Any]) -> None:
            if not isinstance(ev, dict):
                return

            # 策略1: 处理 price_change 或 price_changes 字段
            if ev.get("event_type") == "price_change" or "price_changes" in ev:
                pcs = ev.get("price_changes", [])
                for pc in pcs:
                    asset_id = pc.get("asset_id")
                    if not asset_id:
                        continue
                    parsed = _parse_price_change(pc)
                    with self._lock:
                        self._latest[str(asset_id)] = {
                            "best_bid": parsed.get("best_bid"),
                            "best_ask": parsed.get("best_ask"),
                            "ts": time.time(),
                        }
                    # 统计更新次数
                    self._record_update(str(asset_id))
                return

            # 策略2: 处理单个asset的book/snapshot/delta事件
            # 格式: {"event_type": "book|snapshot|delta", "asset_id": "...", "bids": [...], "asks": [...]}
            event_type = ev.get("event_type", "")
            asset_id = ev.get("asset_id")

            if asset_id and (event_type in ("book", "snapshot", "delta", "market") or "bids" in ev or "asks" in ev):
                # 尝试从bids/asks数组中提取最佳价格
                best_bid = None
                best_ask = None

                # 优先从直接字段读取
                if "best_bid" in ev:
                    best_bid = _to_float_or_none(ev.get("best_bid"))
                if "best_ask" in ev:
                    best_ask = _to_float_or_none(ev.get("best_ask"))

                # 如果没有直接字段，从bids/asks数组中提取
                if best_bid is None and "bids" in ev:
                    bids = ev.get("bids", [])
                    if isinstance(bids, list) and len(bids) > 0:
                        first_bid = bids[0]
                        if isinstance(first_bid, list) and len(first_bid) > 0:
                            best_bid = _to_float_or_none(first_bid[0])
                        elif isinstance(first_bid, dict):
                            best_bid = _to_float_or_none(first_bid.get("price"))

                if best_ask is None and "asks" in ev:
                    asks = ev.get("asks", [])
                    if isinstance(asks, list) and len(asks) > 0:
                        first_ask = asks[0]
                        if isinstance(first_ask, list) and len(first_ask) > 0:
                            best_ask = _to_float_or_none(first_ask[0])
                        elif isinstance(first_ask, dict):
                            best_ask = _to_float_or_none(first_ask.get("price"))

                # 如果从market子对象中读取
                if "market" in ev and isinstance(ev["market"], dict):
                    market = ev["market"]
                    if best_bid is None:
                        best_bid = _to_float_or_none(market.get("best_bid"))
                    if best_ask is None:
                        best_ask = _to_float_or_none(market.get("best_ask"))

                # 至少有一个价格时才更新缓存
                if best_bid is not None or best_ask is not None:
                    with self._lock:
                        # 如果已有缓存，保留未更新的值
                        existing = self._latest.get(str(asset_id), {})
                        self._latest[str(asset_id)] = {
                            "best_bid": best_bid if best_bid is not None else existing.get("best_bid"),
                            "best_ask": best_ask if best_ask is not None else existing.get("best_ask"),
                            "ts": time.time(),
                        }
                    # 统计更新次数
                    self._record_update(str(asset_id))
                    return

            # 策略3: 记录未识别的事件（用于诊断）
            # 只记录可能是market相关的事件（避免噪音）
            if event_type and event_type not in ("pong", "subscribed", "unsubscribed", "heartbeat"):
                self._log(
                    "debug",
                    f"[WS][UNHANDLED_EVENT] type={event_type}, keys={list(ev.keys())}, sample={str(ev)[:200]}"
                )

        try:
            self._ws_watch(
                asset_ids=token_ids,
                label="copytrade",
                on_event=_on_event,
                verbose=False,
                stop_event=stop_event,
            )
        except Exception as exc:
            self._log("warning", f"[ws] watch failed: {exc}")

    def _record_update(self, token_id: str) -> None:
        """记录token的更新次数"""
        with self._stats_lock:
            self._update_count[token_id] = self._update_count.get(token_id, 0) + 1

        # 每30秒报告一次统计
        now = time.time()
        if now - self._last_stats_report >= 30.0:
            self._report_stats()
            self._last_stats_report = now

    def _report_stats(self) -> None:
        """报告缓存更新统计"""
        with self._stats_lock:
            if not self._update_count:
                return

            now = time.time()
            elapsed = now - self._last_stats_time
            if elapsed < 1.0:
                return

            total_updates = sum(self._update_count.values())
            tokens_updated = len(self._update_count)
            rate_per_min = (total_updates / elapsed) * 60.0 if elapsed > 0 else 0.0

            # 找出更新最频繁和最少的token
            sorted_tokens = sorted(self._update_count.items(), key=lambda x: x[1], reverse=True)
            top_tokens = sorted_tokens[:3]
            bottom_tokens = sorted_tokens[-3:] if len(sorted_tokens) > 3 else []

            msg = f"[WS][STATS] 过去{elapsed:.0f}秒: {total_updates}次更新, {tokens_updated}个token, {rate_per_min:.1f}次/分钟"
            if top_tokens:
                top_str = ", ".join([f"{tid[:8]}...({cnt}次)" for tid, cnt in top_tokens])
                msg += f" | 最活跃: {top_str}"
            if bottom_tokens:
                bottom_str = ", ".join([f"{tid[:8]}...({cnt}次)" for tid, cnt in bottom_tokens])
                msg += f" | 最不活跃: {bottom_str}"

            self._log("info", msg)

            # 重置统计
            self._update_count.clear()
            self._last_stats_time = now

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        log_fn = getattr(self._logger, level, None)
        if callable(log_fn):
            log_fn(message)


def _to_float_or_none(val: Any) -> Optional[float]:
    """Helper function to safely convert value to float"""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_price_change(pc: Dict[str, Any]) -> Dict[str, Optional[float]]:
    def _to_float(val: Any) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    price_fields = (
        "last_trade_price",
        "last_price",
        "mark_price",
        "price",
    )
    best_bid_fields = ("best_bid", "bid")
    best_ask_fields = ("best_ask", "ask")

    price_val: Optional[float] = None
    for key in price_fields:
        price_val = _to_float(pc.get(key))
        if price_val is not None:
            break
    if price_val is None:
        bid_val = _to_float(pc.get("best_bid"))
        ask_val = _to_float(pc.get("best_ask"))
        if bid_val is not None and ask_val is not None:
            price_val = (bid_val + ask_val) / 2.0
        elif bid_val is not None:
            price_val = bid_val
        elif ask_val is not None:
            price_val = ask_val

    best_bid_val: Optional[float] = None
    for key in best_bid_fields:
        best_bid_val = _to_float(pc.get(key))
        if best_bid_val is not None:
            break

    best_ask_val: Optional[float] = None
    for key in best_ask_fields:
        best_ask_val = _to_float(pc.get(key))
        if best_ask_val is not None:
            break

    return {
        "price": price_val,
        "best_bid": best_bid_val,
        "best_ask": best_ask_val,
    }
