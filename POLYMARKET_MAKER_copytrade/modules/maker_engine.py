from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional

from maker_execution import (
    maker_buy_follow_bid,
    maker_sell_follow_ask_with_floor_wait,
)

from .topic_selector import Topic


@dataclass
class MakerSession:
    token_id: str
    stop_event: threading.Event
    thread: threading.Thread
    open_size: float = 0.0
    last_buy_price: Optional[float] = None
    last_sell_price: Optional[float] = None
    exit_requested: bool = False
    last_status: str = "IDLE"
    errors: list[str] = field(default_factory=list)


class MakerEngine:
    def __init__(self, client, config: dict, logger=None) -> None:
        self._client = client
        self._config = config
        self._logger = logger
        self._sessions: Dict[str, MakerSession] = {}
        self._lock = threading.Lock()

    def start_topics(self, topics: Iterable[Topic]) -> None:
        for topic in topics:
            token_id = topic.token_id
            if not token_id:
                self._log("warning", f"[maker] 跳过无法解析 token_id 的 topic: {topic.identifier}")
                continue
            with self._lock:
                session = self._sessions.get(token_id)
                if session and session.thread.is_alive():
                    continue
                stop_event = threading.Event()
                thread = threading.Thread(
                    target=self._run_session,
                    name=f"maker-{token_id}",
                    args=(token_id, stop_event),
                    daemon=True,
                )
                session = MakerSession(token_id=token_id, stop_event=stop_event, thread=thread)
                self._sessions[token_id] = session
                thread.start()
                self._log("info", f"[maker] 启动 maker 波段线程 token_id={token_id}")

    def stop_topics(self, topics: Iterable[Topic]) -> None:
        for topic in topics:
            token_id = topic.token_id
            if not token_id:
                continue
            with self._lock:
                session = self._sessions.get(token_id)
                if not session:
                    continue
                session.exit_requested = True
                session.stop_event.set()
                self._log("info", f"[maker] 停止 maker 波段线程 token_id={token_id}")

    def tick(self) -> None:
        with self._lock:
            finished = [
                token_id
                for token_id, session in self._sessions.items()
                if not session.thread.is_alive()
            ]
            for token_id in finished:
                self._sessions.pop(token_id, None)

    def open_positions(self) -> Dict[str, float]:
        with self._lock:
            return {token_id: session.open_size for token_id, session in self._sessions.items()}

    def _run_session(self, token_id: str, stop_event: threading.Event) -> None:
        order_size = float(self._config.get("order_size", 10))
        spread_bps = float(self._config.get("price_spread_bps", 50))
        poll_sec = float(self._config.get("poll_interval_sec", 10))
        min_quote_amount = float(self._config.get("min_quote_amount", 1.0))
        min_order_size = float(self._config.get("min_order_size", 5.0))
        refresh_interval = float(self._config.get("refresh_interval_sec", 5))
        sell_mode = str(self._config.get("sell_mode", "conservative"))
        aggressive_step = float(self._config.get("aggressive_step", 0.01))
        aggressive_timeout = float(self._config.get("aggressive_timeout", 300.0))

        def _stop() -> bool:
            return stop_event.is_set()

        while not stop_event.is_set():
            try:
                buy_result = maker_buy_follow_bid(
                    self._client,
                    token_id,
                    order_size,
                    poll_sec=poll_sec,
                    min_quote_amt=min_quote_amount,
                    min_order_size=min_order_size,
                    stop_check=_stop,
                )
            except Exception as exc:
                self._record_error(token_id, f"BUY 执行异常: {exc}")
                time.sleep(refresh_interval)
                continue

            if stop_event.is_set():
                break

            filled = float(buy_result.get("filled") or 0.0)
            avg_price = buy_result.get("avg_price")
            if filled <= 0 or not avg_price:
                self._log("info", f"[maker] BUY 未成交 token_id={token_id}")
                time.sleep(refresh_interval)
                continue

            with self._lock:
                session = self._sessions.get(token_id)
                if session:
                    session.open_size = filled
                    session.last_buy_price = float(avg_price)
                    session.last_status = "BOUGHT"

            floor_price = float(avg_price) * (1 + spread_bps / 10000.0)

            try:
                sell_result = maker_sell_follow_ask_with_floor_wait(
                    self._client,
                    token_id,
                    filled,
                    floor_price,
                    poll_sec=poll_sec,
                    min_order_size=min_order_size,
                    stop_check=_stop,
                    sell_mode=sell_mode,
                    aggressive_step=aggressive_step,
                    aggressive_timeout=aggressive_timeout,
                )
            except Exception as exc:
                self._record_error(token_id, f"SELL 执行异常: {exc}")
                time.sleep(refresh_interval)
                continue

            sold = float(sell_result.get("filled") or 0.0)
            avg_sell = sell_result.get("avg_price")
            with self._lock:
                session = self._sessions.get(token_id)
                if session:
                    session.open_size = max(session.open_size - sold, 0.0)
                    session.last_sell_price = float(avg_sell) if avg_sell else session.last_sell_price
                    session.last_status = "SOLD" if sold > 0 else "HOLD"

            if stop_event.is_set():
                break

            time.sleep(refresh_interval)

    def _record_error(self, token_id: str, message: str) -> None:
        with self._lock:
            session = self._sessions.get(token_id)
            if session:
                session.errors.append(message)
        self._log("error", f"[maker] token_id={token_id} {message}")

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            print(message)
            return
        log_fn = getattr(self._logger, level, None)
        if callable(log_fn):
            log_fn(message)
        else:
            self._logger.info(message)
