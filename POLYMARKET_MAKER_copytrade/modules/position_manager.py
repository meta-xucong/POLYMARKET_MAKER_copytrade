from __future__ import annotations

import threading
from typing import Iterable, Optional

from maker_execution import maker_sell_follow_ask_with_floor_wait

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
            token_id = topic.token_id
            if not token_id:
                continue
            position_size = self._maker_engine.open_positions().get(token_id, 0.0)
            if position_size <= 0:
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
        min_order_size = float(self._config.get("min_order_size", 5.0))
        sell_mode = str(self._config.get("exit_sell_mode", "aggressive"))
        aggressive_step = float(self._config.get("aggressive_step", 0.01))
        aggressive_timeout = float(self._config.get("aggressive_timeout", 300.0))

        floor_price = float(self._config.get("exit_floor_price", 0.0))
        try:
            maker_sell_follow_ask_with_floor_wait(
                self._client,
                token_id,
                position_size,
                floor_price,
                poll_sec=poll_sec,
                min_order_size=min_order_size,
                sell_mode=sell_mode,
                aggressive_step=aggressive_step,
                aggressive_timeout=aggressive_timeout,
            )
            self._log("info", f"[position] 已触发清仓 token_id={token_id}")
        except Exception as exc:
            self._log("error", f"[position] 清仓失败 token_id={token_id}: {exc}")
        finally:
            if self._exit_semaphore:
                self._exit_semaphore.release()

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            print(message)
            return
        log_fn = getattr(self._logger, level, None)
        if callable(log_fn):
            log_fn(message)
        else:
            self._logger.info(message)
