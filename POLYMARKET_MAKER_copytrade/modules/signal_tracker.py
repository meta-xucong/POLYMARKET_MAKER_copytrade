from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from copytrade_v3_muti.ct_data import fetch_target_actions_since

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
        blacklist_token_keys: Optional[Iterable[str]] = None,
        blacklist_token_ids: Optional[Iterable[str]] = None,
    ) -> None:
        self._client = client
        self._target_accounts = [str(addr).strip() for addr in target_accounts if str(addr).strip()]
        self._poll_interval_sec = max(float(poll_interval_sec), 0.1)
        self._logger = logger
        self._blacklist_token_keys = [
            str(key).strip() for key in (blacklist_token_keys or []) if str(key).strip()
        ]
        self._blacklist_token_ids = [
            str(key).strip() for key in (blacklist_token_ids or []) if str(key).strip()
        ]
        cursor_seed = int(initial_cursor_ms) if initial_cursor_ms is not None else int(time.time() * 1000)
        self._cursor_ms: Dict[str, int] = {addr: cursor_seed for addr in self._target_accounts}

    @property
    def poll_interval_sec(self) -> float:
        return self._poll_interval_sec

    def update_config(
        self,
        *,
        poll_interval_sec: Optional[float] = None,
        blacklist_token_keys: Optional[Iterable[str]] = None,
        blacklist_token_ids: Optional[Iterable[str]] = None,
    ) -> None:
        if poll_interval_sec is not None:
            self._poll_interval_sec = max(float(poll_interval_sec), 0.1)
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
        return events

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            print(message)
            return
        log_fn = getattr(self._logger, level, None)
        if callable(log_fn):
            log_fn(message)
        else:
            self._logger.info(message)
