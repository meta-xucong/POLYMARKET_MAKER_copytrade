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
    ) -> None:
        self._ws_watch = ws_watch
        self._logger = logger
        self._stale_sec = stale_sec
        self._lock = threading.Lock()
        self._latest: Dict[str, Dict[str, Any]] = {}
        self._tokens: Set[str] = set()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None

    def update_tokens(self, token_ids: Iterable[str]) -> None:
        normalized = {str(tid) for tid in token_ids if tid}
        with self._lock:
            if normalized == self._tokens:
                return
            self._tokens = normalized
        self._restart()

    def get_best(self, token_id: str) -> Tuple[Optional[float], Optional[float]]:
        now = time.time()
        with self._lock:
            entry = self._latest.get(str(token_id))
            if not entry:
                return None, None
            ts = float(entry.get("ts") or 0.0)
            if ts and self._stale_sec > 0 and now - ts > self._stale_sec:
                return None, None
            return entry.get("best_bid"), entry.get("best_ask")

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
            if ev.get("event_type") == "price_change":
                pcs = ev.get("price_changes", [])
            elif "price_changes" in ev:
                pcs = ev.get("price_changes", [])
            else:
                return

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

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        log_fn = getattr(self._logger, level, None)
        if callable(log_fn):
            log_fn(message)


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
