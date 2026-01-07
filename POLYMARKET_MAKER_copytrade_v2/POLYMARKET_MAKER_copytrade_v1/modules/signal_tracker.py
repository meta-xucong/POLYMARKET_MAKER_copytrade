from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from copytrade_v3_muti.ct_data import fetch_target_actions_since
from copytrade_v3_muti.ct_data import fetch_positions_norm

from .topic_selector import Topic, select_topics


@dataclass(frozen=True)
class SignalEvent:
    signal_type: str
    topics: List[Topic]
    timestamp: int
    source_account: str


class SignalTracker:
    def __init__(
        self,
        client,
        target_accounts: Iterable[str],
        *,
        poll_interval_sec: float = 5.0,
        logger=None,
        initial_cursor_ms: Optional[int] = None,
        position_poll_interval_sec: float = 20.0,
        position_size_threshold: float = 0.0,
        positions_refresh_sec: Optional[int] = None,
        positions_cache_bust_mode: str = "sec",
        sell_confirm_max: int = 5,
        sell_confirm_window_sec: int = 300,
        sell_confirm_force_ratio: float = 0.5,
        sell_confirm_force_shares: float = 0.0,
        blacklist_token_keys: Optional[Iterable[str]] = None,
        blacklist_token_ids: Optional[Iterable[str]] = None,
    ) -> None:
        self._client = client
        self._target_accounts = [str(addr).strip() for addr in target_accounts if str(addr).strip()]
        self._poll_interval_sec = max(float(poll_interval_sec), 0.1)
        self._position_poll_interval_sec = max(float(position_poll_interval_sec), 0.1)
        self._position_size_threshold = float(position_size_threshold or 0.0)
        self._positions_refresh_sec = positions_refresh_sec
        self._positions_cache_bust_mode = str(positions_cache_bust_mode or "sec")
        self._sell_confirm_max = max(int(sell_confirm_max), 1)
        self._sell_confirm_window_sec = max(int(sell_confirm_window_sec), 0)
        self._sell_confirm_force_ratio = float(sell_confirm_force_ratio or 0.0)
        self._sell_confirm_force_shares = float(sell_confirm_force_shares or 0.0)
        self._logger = logger
        self._blacklist_token_keys = [
            str(key).strip() for key in (blacklist_token_keys or []) if str(key).strip()
        ]
        self._blacklist_token_ids = [
            str(key).strip() for key in (blacklist_token_ids or []) if str(key).strip()
        ]
        cursor_seed = int(initial_cursor_ms) if initial_cursor_ms is not None else int(time.time() * 1000)
        self._cursor_ms: Dict[str, int] = {addr: cursor_seed for addr in self._target_accounts}
        self._positions_cursor: Dict[str, float] = {addr: 0.0 for addr in self._target_accounts}
        self._last_positions: Dict[str, Dict[str, float]] = {}
        self._sell_confirm: Dict[str, Dict[str, Dict[str, int]]] = {}
        self._token_id_by_key: Dict[str, str] = {}

    @property
    def poll_interval_sec(self) -> float:
        return self._poll_interval_sec

    def update_config(
        self,
        *,
        poll_interval_sec: Optional[float] = None,
        position_poll_interval_sec: Optional[float] = None,
        position_size_threshold: Optional[float] = None,
        positions_refresh_sec: Optional[int] = None,
        positions_cache_bust_mode: Optional[str] = None,
        sell_confirm_max: Optional[int] = None,
        sell_confirm_window_sec: Optional[int] = None,
        sell_confirm_force_ratio: Optional[float] = None,
        sell_confirm_force_shares: Optional[float] = None,
        blacklist_token_keys: Optional[Iterable[str]] = None,
        blacklist_token_ids: Optional[Iterable[str]] = None,
    ) -> None:
        if poll_interval_sec is not None:
            self._poll_interval_sec = max(float(poll_interval_sec), 0.1)
        if position_poll_interval_sec is not None:
            self._position_poll_interval_sec = max(float(position_poll_interval_sec), 0.1)
        if position_size_threshold is not None:
            self._position_size_threshold = float(position_size_threshold or 0.0)
        if positions_refresh_sec is not None:
            self._positions_refresh_sec = positions_refresh_sec
        if positions_cache_bust_mode is not None:
            self._positions_cache_bust_mode = str(positions_cache_bust_mode or "sec")
        if sell_confirm_max is not None:
            self._sell_confirm_max = max(int(sell_confirm_max), 1)
        if sell_confirm_window_sec is not None:
            self._sell_confirm_window_sec = max(int(sell_confirm_window_sec), 0)
        if sell_confirm_force_ratio is not None:
            self._sell_confirm_force_ratio = float(sell_confirm_force_ratio or 0.0)
        if sell_confirm_force_shares is not None:
            self._sell_confirm_force_shares = float(sell_confirm_force_shares or 0.0)
        if blacklist_token_keys is not None:
            self._blacklist_token_keys = [
                str(key).strip()
                for key in (blacklist_token_keys or [])
                if str(key).strip()
            ]
        if blacklist_token_ids is not None:
            self._blacklist_token_ids = [
                str(key).strip()
                for key in (blacklist_token_ids or [])
                if str(key).strip()
            ]

    def poll(self) -> List[SignalEvent]:
        events: List[SignalEvent] = []
        for account in self._target_accounts:
            since_ms = int(self._cursor_ms.get(account, 0))
            try:
                actions, info = fetch_target_actions_since(self._client, account, since_ms)
            except Exception as exc:
                self._log("error", f"[signal_tracker] fetch_target_actions_since failed: {exc}")
                continue

            latest_ms = int(info.get("latest_ms") or 0)
            if latest_ms > since_ms:
                self._cursor_ms[account] = latest_ms

            if not actions:
                continue

            grouped: Dict[str, List[Dict[str, object]]] = {"BUY": [], "SELL": []}
            for action in actions:
                side = str(action.get("side") or "").upper()
                if side in grouped:
                    grouped[side].append(action)
                self._capture_token_key_mapping(action)

            for side, side_actions in grouped.items():
                if not side_actions:
                    continue
                topics = select_topics(
                    side_actions,
                    blacklist_token_keys=self._blacklist_token_keys,
                    blacklist_token_ids=self._blacklist_token_ids,
                )
                if not topics:
                    continue
                latest_ts = 0
                for action in side_actions:
                    ts = action.get("timestamp")
                    if hasattr(ts, "timestamp"):
                        ts_val = int(ts.timestamp())
                    else:
                        try:
                            ts_val = int(ts)  # type: ignore[arg-type]
                        except (TypeError, ValueError):
                            ts_val = 0
                    latest_ts = max(latest_ts, ts_val)
                events.append(
                    SignalEvent(
                        signal_type=side,
                        topics=topics,
                        timestamp=latest_ts,
                        source_account=account,
                    )
                )

            forced_sells = self._poll_positions_forced_sells(account, grouped.get("SELL") or [])
            if forced_sells:
                events.append(
                    SignalEvent(
                        signal_type="SELL",
                        topics=forced_sells,
                        timestamp=int(time.time()),
                        source_account=account,
                    )
                )
        return events

    def _poll_positions_forced_sells(
        self,
        account: str,
        sell_actions: List[Dict[str, object]],
    ) -> List[Topic]:
        now = time.time()
        last_poll = self._positions_cursor.get(account, 0.0)
        if now - last_poll < self._position_poll_interval_sec:
            return []
        self._positions_cursor[account] = now

        try:
            positions, info = fetch_positions_norm(
                self._client,
                account,
                self._position_size_threshold,
                refresh_sec=self._positions_refresh_sec,
                cache_bust_mode=self._positions_cache_bust_mode,
            )
        except Exception as exc:
            self._log("warning", f"[signal_tracker] fetch_positions_norm failed: {exc}")
            return []

        if not positions:
            return []

        current: Dict[str, float] = {}
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            token_key = pos.get("token_key")
            if not token_key:
                continue
            try:
                size = float(pos.get("size") or 0.0)
            except (TypeError, ValueError):
                continue
            current[str(token_key)] = size
            self._capture_token_key_mapping(pos)

        previous = self._last_positions.get(account, {})
        self._last_positions[account] = current
        if not previous:
            return []

        sell_keys = {
            key for key in (self._action_token_key(action) for action in sell_actions) if key
        }
        forced_topics: List[Topic] = []
        for token_key, prev_size in previous.items():
            now_size = current.get(token_key, 0.0)
            delta = now_size - float(prev_size)
            if delta >= -1e-9:
                self._sell_confirm_forget(account, token_key)
                continue
            if token_key in sell_keys:
                self._sell_confirm_forget(account, token_key)
                continue
            if not self._sell_confirm_accept(account, token_key, float(prev_size), -delta):
                continue
            topic = self._topic_from_token_key(token_key)
            if topic:
                forced_topics.append(topic)
        return forced_topics

    def _sell_confirm_accept(
        self,
        account: str,
        token_key: str,
        prev_size: float,
        drop_shares: float,
    ) -> bool:
        now_ts = int(time.time())
        sell_confirm = self._sell_confirm.setdefault(account, {})
        meta = sell_confirm.get(token_key) or {"count": 0, "first_ts": now_ts}
        first_ts = int(meta.get("first_ts") or now_ts)
        if self._sell_confirm_window_sec > 0 and now_ts - first_ts > self._sell_confirm_window_sec:
            meta = {"count": 0, "first_ts": now_ts}
        meta["count"] = int(meta.get("count") or 0) + 1
        meta["first_ts"] = int(meta.get("first_ts") or now_ts)
        sell_confirm[token_key] = meta
        if meta["count"] < self._sell_confirm_max:
            self._log(
                "info",
                f"[signal_tracker] HOLD token_key={token_key} reason=no_sell_action "
                f"confirm={meta['count']}/{self._sell_confirm_max}",
            )
            return False

        threshold = 0.0
        if self._sell_confirm_force_ratio > 0 and prev_size > 0:
            threshold = max(threshold, prev_size * self._sell_confirm_force_ratio)
        if self._sell_confirm_force_shares > 0:
            threshold = max(threshold, self._sell_confirm_force_shares)
        if threshold > 0 and drop_shares < threshold:
            self._log(
                "info",
                f"[signal_tracker] HOLD token_key={token_key} reason=drop_below_threshold "
                f"drop={drop_shares:.6f} threshold={threshold:.6f}",
            )
            meta["count"] = self._sell_confirm_max
            sell_confirm[token_key] = meta
            return False

        self._sell_confirm_forget(account, token_key)
        self._log(
            "warning",
            f"[signal_tracker] FORCE_SELL token_key={token_key} drop={drop_shares:.6f}",
        )
        return True

    def _sell_confirm_forget(self, account: str, token_key: str) -> None:
        if account in self._sell_confirm:
            self._sell_confirm[account].pop(token_key, None)

    def _action_token_key(self, action: Dict[str, object]) -> Optional[str]:
        token_key = action.get("token_key")
        if token_key:
            return str(token_key)
        condition_id = action.get("condition_id")
        outcome_index = action.get("outcome_index")
        if condition_id is None or outcome_index is None:
            return None
        try:
            idx = int(outcome_index)
        except (TypeError, ValueError):
            return None
        return f"{condition_id}:{idx}"

    def _capture_token_key_mapping(self, action: Dict[str, object]) -> None:
        token_key = action.get("token_key") or self._action_token_key(action)
        token_id = action.get("token_id") or action.get("tokenId") or action.get("asset")
        if token_key and token_id:
            self._token_id_by_key[str(token_key)] = str(token_id)

    def _topic_from_token_key(self, token_key: str) -> Optional[Topic]:
        token_id = self._token_id_by_key.get(token_key)
        condition_id = None
        outcome_index = None
        if ":" in token_key:
            condition_id, idx = token_key.split(":", 1)
            try:
                outcome_index = int(idx)
            except ValueError:
                outcome_index = None
        return Topic(
            token_id=token_id,
            token_key=token_key,
            condition_id=condition_id,
            outcome_index=outcome_index,
            price=None,
        )

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            print(message)
            return
        log_fn = getattr(self._logger, level, None)
        if callable(log_fn):
            log_fn(message)
        else:
            self._logger.info(message)
