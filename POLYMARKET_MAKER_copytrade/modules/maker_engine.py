from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple
from zoneinfo import ZoneInfo

from maker_execution import (
    maker_buy_follow_bid,
    maker_sell_follow_ask_with_floor_wait,
    _fetch_best_price,
)
from Volatility_arbitrage_strategy import ActionType, StrategyConfig, VolArbStrategy

from copytrade_v3_muti.ct_resolver import gamma_fetch_markets_by_clob_token_ids, market_tradeable_state
from copytrade_v3_muti.ct_risk import risk_check

from .topic_selector import Topic


@dataclass
class MakerSession:
    token_id: str
    token_key: Optional[str]
    stop_event: threading.Event
    thread: threading.Thread
    strategy: VolArbStrategy
    open_size: float = 0.0
    last_buy_price: Optional[float] = None
    last_sell_price: Optional[float] = None
    exit_requested: bool = False
    last_status: str = "IDLE"
    errors: list[str] = field(default_factory=list)
    sell_only_start_ts: Optional[float] = None


class MakerEngine:
    def __init__(
        self,
        client,
        config: dict,
        *,
        run_params: Optional[dict] = None,
        strategy_defaults: Optional[dict] = None,
        risk_config: Optional[dict] = None,
        scheduler_config: Optional[dict] = None,
        orderbook_config: Optional[dict] = None,
        ws_cache: Optional[object] = None,
        state: Optional[dict] = None,
        state_lock: Optional[threading.Lock] = None,
        position_fetcher: Optional[callable] = None,
        logger=None,
    ) -> None:
        self._client = client
        self._config = config
        self._run_params = run_params or {}
        self._strategy_defaults = strategy_defaults or {}
        self._risk_config = risk_config or {}
        self._scheduler_config = scheduler_config or {}
        self._orderbook_config = orderbook_config or {}
        self._ws_cache = ws_cache
        self._state = state or {}
        self._state_lock = state_lock or threading.Lock()
        self._position_fetcher = position_fetcher
        self._logger = logger
        self._sessions: Dict[str, MakerSession] = {}
        self._lock = threading.Lock()
        self._session_semaphore: Optional[threading.Semaphore] = None
        self._start_cooldowns: Dict[str, float] = {}
        self._orderbook_cache: Dict[str, Tuple[float, float, float]] = {}
        max_jobs = int(self._scheduler_config.get("max_concurrent_jobs") or 0)
        if max_jobs > 0:
            self._reset_session_semaphore(max_jobs)

    def update_config(
        self,
        config: dict,
        *,
        run_params: Optional[dict] = None,
        strategy_defaults: Optional[dict] = None,
        risk_config: Optional[dict] = None,
        scheduler_config: Optional[dict] = None,
        orderbook_config: Optional[dict] = None,
        ws_cache: Optional[object] = None,
        position_fetcher: Optional[callable] = None,
    ) -> None:
        self._config = config
        if run_params is not None:
            self._run_params = run_params
        if strategy_defaults is not None:
            self._strategy_defaults = strategy_defaults
        if risk_config is not None:
            self._risk_config = risk_config
        if scheduler_config is not None:
            self._scheduler_config = scheduler_config
            max_jobs = int(self._scheduler_config.get("max_concurrent_jobs") or 0)
            self._reset_session_semaphore(max_jobs)
        if orderbook_config is not None:
            self._orderbook_config = orderbook_config
        if ws_cache is not None:
            self._ws_cache = ws_cache
        if position_fetcher is not None:
            self._position_fetcher = position_fetcher

    def update_position_fetcher(self, position_fetcher: Optional[callable]) -> None:
        self._position_fetcher = position_fetcher

    def _build_position_probe(self, token_id: str) -> Optional[callable]:
        if not self._position_fetcher:
            return None
        refresh_sec = float(
            self._config.get("buy_position_refresh_sec")
            or self._config.get("exit_position_refresh_sec")
            or 30.0
        )
        refresh_sec = max(refresh_sec, 0.0)
        last_ts = 0.0
        last_val: Optional[float] = None

        def _probe() -> Optional[float]:
            nonlocal last_ts, last_val
            now = time.time()
            if refresh_sec == 0.0 or now - last_ts >= refresh_sec:
                try:
                    last_val = self._position_fetcher(token_id)
                except Exception as exc:
                    self._log("warning", f"[maker] 持仓校对失败 token_id={token_id}: {exc}")
                    last_val = None
                last_ts = now
            return last_val

        return _probe

    def start_topics(self, topics: Iterable[Topic]) -> None:
        for topic in topics:
            token_id = topic.token_id
            if not token_id:
                self._log("warning", f"[maker] 跳过无法解析 token_id 的 topic: {topic.identifier}")
                continue
            if self._session_semaphore and not self._session_semaphore.acquire(blocking=False):
                self._log("warning", f"[maker] 并发已满，跳过 token_id={token_id}")
                continue
            now = time.time()
            cooldown = float(self._scheduler_config.get("topic_start_cooldown_sec") or 0)
            last_start = self._start_cooldowns.get(token_id)
            if cooldown > 0 and last_start and now - last_start < cooldown:
                if self._session_semaphore:
                    self._session_semaphore.release()
                self._log("info", f"[maker] token_id={token_id} 处于启动冷却中")
                continue
            with self._lock:
                session = self._sessions.get(token_id)
                if session and session.thread.is_alive():
                    if self._session_semaphore:
                        self._session_semaphore.release()
                    continue
                stop_event = threading.Event()
                session_config = self._resolve_strategy_config(topic)
                strategy = self._build_strategy(token_id, session_config)
                thread = threading.Thread(
                    target=self._run_session,
                    name=f"maker-{token_id}",
                    args=(token_id, stop_event),
                    daemon=True,
                )
                session = MakerSession(
                    token_id=token_id,
                    token_key=topic.token_key,
                    stop_event=stop_event,
                    thread=thread,
                    strategy=strategy,
                )
                self._sessions[token_id] = session
                self._start_cooldowns[token_id] = now
                thread.start()
                self._log("info", f"[maker] 启动 maker 波段线程 token_id={token_id}")
        self._refresh_ws_tokens()

    def start_existing_position(
        self,
        topic: Topic,
        position_size: float,
        *,
        hold_until_sell_signal: bool = False,
    ) -> None:
        token_id = topic.token_id
        if not token_id:
            self._log("warning", f"[maker] 跳过无法解析 token_id 的历史仓位: {topic.identifier}")
            return
        if self._session_semaphore and not self._session_semaphore.acquire(blocking=False):
            self._log("warning", f"[maker] 并发已满，跳过历史仓位 token_id={token_id}")
            return
        now = time.time()
        with self._lock:
            session = self._sessions.get(token_id)
            if session and session.thread.is_alive():
                session.open_size = max(float(position_size), 0.0)
                session.strategy.sync_position(session.open_size)
                if hold_until_sell_signal:
                    session.strategy.stop("await target sell")
                else:
                    session.strategy.resume()
                if self._session_semaphore:
                    self._session_semaphore.release()
                return
            stop_event = threading.Event()
            session_config = self._resolve_strategy_config(topic)
            strategy = self._build_strategy(token_id, session_config)
            strategy.sync_position(max(float(position_size), 0.0))
            if hold_until_sell_signal:
                strategy.stop("await target sell")
            thread = threading.Thread(
                target=self._run_session,
                name=f"maker-{token_id}",
                args=(token_id, stop_event),
                daemon=True,
            )
            session = MakerSession(
                token_id=token_id,
                token_key=topic.token_key,
                stop_event=stop_event,
                thread=thread,
                strategy=strategy,
                open_size=max(float(position_size), 0.0),
                last_status="BOOT",
            )
            self._sessions[token_id] = session
            self._start_cooldowns[token_id] = now
            thread.start()
            self._log(
                "info",
                f"[maker] 接管历史仓位 token_id={token_id} size={position_size}",
            )
        self._refresh_ws_tokens()

    def stop_topics(self, topics: Iterable[Topic]) -> None:
        for topic in topics:
            token_ids = self.match_token_ids(topic)
            if not token_ids:
                continue
            for token_id in token_ids:
                with self._lock:
                    session = self._sessions.get(token_id)
                    if not session:
                        continue
                    session.exit_requested = True
                    session.stop_event.set()
                    self._log("info", f"[maker] 停止 maker 波段线程 token_id={token_id}")
        self._refresh_ws_tokens()

    def match_token_ids(self, topic: Topic) -> list[str]:
        if topic.token_id:
            return [topic.token_id]
        if not topic.token_key:
            return []
        matched = []
        with self._lock:
            for token_id, session in self._sessions.items():
                if session.token_key and session.token_key == topic.token_key:
                    matched.append(token_id)
        return matched

    def tick(self) -> None:
        with self._lock:
            finished = [
                token_id
                for token_id, session in self._sessions.items()
                if not session.thread.is_alive()
            ]
            for token_id in finished:
                self._sessions.pop(token_id, None)
        self._refresh_ws_tokens()

    def open_positions(self) -> Dict[str, float]:
        with self._lock:
            return {token_id: session.open_size for token_id, session in self._sessions.items()}

    def update_open_size(self, token_id: str, open_size: float) -> None:
        with self._lock:
            session = self._sessions.get(token_id)
            if not session:
                return
            session.open_size = max(float(open_size), 0.0)

    def _refresh_ws_tokens(self) -> None:
        ws_cache = self._ws_cache
        if ws_cache is None:
            return
        with self._lock:
            token_ids = list(self._sessions.keys())
        try:
            ws_cache.update_tokens(token_ids)
        except Exception:
            return

    def _reset_session_semaphore(self, max_jobs: int) -> None:
        if max_jobs <= 0:
            self._session_semaphore = None
            return
        active = self._active_session_count()
        self._session_semaphore = threading.Semaphore(max_jobs)
        to_acquire = min(active, max_jobs)
        for _ in range(to_acquire):
            self._session_semaphore.acquire()
        if active > max_jobs:
            self._log(
                "warning",
                f"[maker] 并发上限已低于当前活跃数 active={active} max={max_jobs}",
            )

    def _active_session_count(self) -> int:
        with self._lock:
            return sum(1 for session in self._sessions.values() if session.thread.is_alive())

    def _run_session(self, token_id: str, stop_event: threading.Event) -> None:
        try:
            self._run_session_loop(token_id, stop_event)
        finally:
            if self._session_semaphore:
                self._session_semaphore.release()

    def _run_session_loop(self, token_id: str, stop_event: threading.Event) -> None:
        cfg = self._config
        run_params = self._run_params
        order_size = float(run_params.get("order_size") or cfg.get("order_size", 10))
        spread_bps = float(cfg.get("price_spread_bps", 50))
        poll_sec = float(cfg.get("poll_interval_sec", 10))
        min_quote_amount = float(cfg.get("min_quote_amount", 1.0))
        min_order_size = float(cfg.get("min_order_size", 5.0))
        refresh_interval = float(cfg.get("refresh_interval_sec", 5))
        sell_mode = str(run_params.get("sell_mode") or cfg.get("sell_mode", "conservative"))
        aggressive_step = float(cfg.get("aggressive_step", 0.01))
        aggressive_timeout = float(cfg.get("aggressive_timeout", 300.0))

        def _stop() -> bool:
            return stop_event.is_set()

        while not stop_event.is_set():
            session = self._sessions.get(token_id)
            if session is None:
                break
            ws_cache = self._ws_cache
            best_bid_fn = None
            best_ask_fn = None
            if ws_cache is not None:
                best_bid_fn = lambda: ws_cache.get_best(token_id)[0]  # noqa: E731
                best_ask_fn = lambda: ws_cache.get_best(token_id)[1]  # noqa: E731
            best_bid, best_ask = self._get_best_prices(token_id)
            if best_bid is None or best_ask is None:
                self._log("debug", f"[maker] token_id={token_id} 盘口缺失 bid={best_bid} ask={best_ask}")
                time.sleep(refresh_interval)
                continue

            self._update_sell_only(session)

            action = session.strategy.on_tick(best_ask=best_ask, best_bid=best_bid, ts=time.time())
            if action is None:
                self._log(
                    "debug",
                    f"[maker] token_id={token_id} no_action bid={best_bid} ask={best_ask}",
                )
                time.sleep(refresh_interval)
                continue
            self._log(
                "debug",
                f"[maker] token_id={token_id} action={action.action.value} bid={best_bid} ask={best_ask}",
            )

            if action.action == ActionType.BUY:
                position_probe = self._build_position_probe(token_id)
                capped_order_size = self._cap_buy_order_size(session, order_size, best_bid)
                if capped_order_size <= 0:
                    session.strategy.on_reject("risk_blocked")
                    time.sleep(refresh_interval)
                    continue
                if not self._risk_allows_buy(session, capped_order_size, best_bid):
                    session.strategy.on_reject("risk_blocked")
                    time.sleep(refresh_interval)
                    continue
                try:
                    buy_result = maker_buy_follow_bid(
                        self._client,
                        token_id,
                        capped_order_size,
                        poll_sec=poll_sec,
                        min_quote_amt=min_quote_amount,
                        min_order_size=min_order_size,
                        stop_check=_stop,
                        best_bid_fn=best_bid_fn,
                        external_fill_probe=position_probe,
                    )
                except Exception as exc:
                    self._record_error(token_id, f"BUY 执行异常: {exc}")
                    session.strategy.on_reject(str(exc))
                    time.sleep(refresh_interval)
                    continue

                filled = float(buy_result.get("filled") or 0.0)
                avg_price = buy_result.get("avg_price")
                if filled <= 0:
                    self._log("info", f"[maker] BUY 未成交 token_id={token_id}")
                    session.strategy.on_reject("not_filled")
                    time.sleep(refresh_interval)
                    continue
                if avg_price is None:
                    avg_price = float(best_bid)
                    self._log(
                        "warning",
                        f"[maker] BUY 成交均价缺失，使用参考价={avg_price:.6f} token_id={token_id}",
                    )

                with self._lock:
                    new_total_position = max(session.open_size + filled, filled)
                    session.open_size = new_total_position
                    session.last_buy_price = float(avg_price)
                    session.last_status = "BOUGHT"
                session.strategy.on_buy_filled(
                    float(avg_price), size=filled, total_position=new_total_position
                )
                self._record_cumulative_buy(session, filled, float(avg_price))

            elif action.action == ActionType.SELL:
                position_probe = self._build_position_probe(token_id)
                floor_price = session.strategy.sell_trigger_price()
                if floor_price is None:
                    floor_price = float(best_bid) * (1 + spread_bps / 10000.0)
                position_size = session.open_size
                if position_size <= 0:
                    session.strategy.on_reject("no_position")
                    time.sleep(refresh_interval)
                    continue
                if not self._risk_allows_sell(session, position_size, best_bid):
                    session.strategy.on_reject("risk_blocked")
                    time.sleep(refresh_interval)
                    continue
                try:
                    sell_result = maker_sell_follow_ask_with_floor_wait(
                        self._client,
                        token_id,
                        position_size,
                        floor_price,
                        poll_sec=poll_sec,
                        min_order_size=min_order_size,
                        stop_check=_stop,
                        sell_mode=sell_mode,
                        aggressive_step=aggressive_step,
                        aggressive_timeout=aggressive_timeout,
                        best_ask_fn=best_ask_fn,
                        position_fetcher=position_probe,
                        position_refresh_interval=float(
                            self._config.get("exit_position_refresh_sec") or 30.0
                        ),
                    )
                except Exception as exc:
                    self._record_error(token_id, f"SELL 执行异常: {exc}")
                    session.strategy.on_reject(str(exc))
                    time.sleep(refresh_interval)
                    continue

                sold = float(sell_result.get("filled") or 0.0)
                avg_sell = sell_result.get("avg_price")
                remaining = float(sell_result.get("remaining") or 0.0)
                remaining_for_strategy = remaining if remaining > 0 else None
                session.strategy.on_sell_filled(
                    avg_price=float(avg_sell) if avg_sell else None,
                    size=sold if sold > 0 else None,
                    remaining=remaining_for_strategy,
                )
                with self._lock:
                    session.open_size = max(session.open_size - sold, 0.0)
                    session.last_sell_price = (
                        float(avg_sell) if avg_sell else session.last_sell_price
                    )
                    session.last_status = "SOLD" if sold > 0 else "HOLD"

            time.sleep(refresh_interval)

    def _resolve_strategy_config(self, topic: Topic) -> Dict[str, Any]:
        def _float_or_none(value: Any) -> Optional[float]:
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        base = dict(self._config)
        defaults = self._strategy_defaults or {}
        default_cfg = defaults.get("default") if isinstance(defaults, dict) else None
        if isinstance(default_cfg, dict):
            base.update(default_cfg)
        topics_cfg = defaults.get("topics") if isinstance(defaults, dict) else None
        if isinstance(topics_cfg, dict):
            for key in (topic.identifier, topic.token_id, topic.token_key):
                if key and key in topics_cfg and isinstance(topics_cfg[key], dict):
                    base.update(topics_cfg[key])
                    break
        low_price_cfg = defaults.get("low_price") if isinstance(defaults, dict) else None
        if isinstance(low_price_cfg, dict):
            price_threshold = _float_or_none(low_price_cfg.get("price_threshold"))
            if price_threshold is None:
                price_threshold = 0.15
            low_order_size = _float_or_none(low_price_cfg.get("order_size"))
            if low_order_size is None:
                low_order_size = 5.0
            if topic.price is not None and topic.price < price_threshold:
                base["order_size"] = low_order_size
        return base

    def _build_strategy(self, token_id: str, cfg: Dict[str, Any]) -> VolArbStrategy:
        def _float_or_none(value: Any) -> Optional[float]:
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        strategy_cfg = StrategyConfig(
            token_id=token_id,
            buy_price_threshold=_float_or_none(cfg.get("buy_price_threshold")),
            profit_ratio=float(cfg.get("profit_ratio", 0.05)),
            drop_window_minutes=float(cfg.get("drop_window_minutes", 10.0)),
            drop_pct=float(cfg.get("drop_pct", 0.05)),
            profit_pct=_float_or_none(cfg.get("profit_pct")),
            max_history_points=int(cfg.get("max_history_points", 600)),
            enable_incremental_drop_pct=bool(cfg.get("enable_incremental_drop_pct", False)),
            incremental_drop_pct_step=float(cfg.get("incremental_drop_pct_step", 0.001)),
            incremental_drop_pct_cap=float(cfg.get("incremental_drop_pct_cap", 0.20)),
            disable_duplicate_signal=bool(cfg.get("disable_duplicate_signal", True)),
            disable_sell_signals=bool(cfg.get("disable_sell_signals", False)),
            min_price=_float_or_none(cfg.get("min_price", 0.0)),
            max_price=_float_or_none(cfg.get("max_price", 1.0)),
            min_market_order_size=_float_or_none(cfg.get("min_market_order_size")),
        )
        return VolArbStrategy(strategy_cfg)

    def _get_best_prices(self, token_id: str) -> Tuple[Optional[float], Optional[float]]:
        ws_cache = self._ws_cache
        if ws_cache is not None:
            try:
                ws_bid, ws_ask = ws_cache.get_best(token_id)
            except Exception:
                ws_bid, ws_ask = None, None
            if ws_bid is not None and ws_ask is not None:
                return ws_bid, ws_ask
        refresh_sec = float(self._orderbook_config.get("refresh_sec") or 0.0)
        now = time.time()
        cached = self._orderbook_cache.get(token_id)
        if cached and refresh_sec > 0 and now - cached[0] < refresh_sec:
            return cached[1], cached[2]

        bid_sample = _fetch_best_price(self._client, token_id, "bid")
        ask_sample = _fetch_best_price(self._client, token_id, "ask")
        bid = bid_sample.price if bid_sample is not None else None
        ask = ask_sample.price if ask_sample is not None else None
        if bid is None or ask is None:
            return bid, ask
        self._orderbook_cache[token_id] = (now, bid, ask)
        cache_max = int(self._orderbook_config.get("cache_max_items") or 0)
        if cache_max > 0 and len(self._orderbook_cache) > cache_max:
            overflow = len(self._orderbook_cache) - cache_max
            for key in list(self._orderbook_cache.keys())[:overflow]:
                self._orderbook_cache.pop(key, None)
        return bid, ask

    def _risk_allows_buy(self, session: MakerSession, order_size: float, ref_price: float) -> bool:
        token_key = session.token_key or session.token_id
        with self._state_lock:
            total_usd = float(self._state.get("cumulative_buy_usd_total") or 0.0)
            token_usd_map = self._state.get("cumulative_buy_usd_by_token") or {}
            token_usd = float(token_usd_map.get(token_key) or 0.0)
        allowed, reason = risk_check(
            token_key=token_key or "",
            order_shares=order_size,
            my_shares=session.open_size,
            ref_price=ref_price,
            cfg=self._risk_config,
            side="BUY",
            cumulative_total_usd=total_usd,
            cumulative_token_usd=token_usd,
        )
        self._log(
            "debug",
            (
                "[risk] BUY token_id=%s allowed=%s reason=%s order_size=%.6f open_size=%.6f "
                "ref_price=%.6f total_usd=%.6f token_usd=%.6f"
            )
            % (
                session.token_id,
                allowed,
                reason,
                order_size,
                session.open_size,
                ref_price,
                total_usd,
                token_usd,
            ),
        )
        if not allowed:
            self._log("info", f"[risk] BUY 拒绝 token_id={session.token_id} reason={reason}")
        return allowed

    def _risk_allows_sell(self, session: MakerSession, order_size: float, ref_price: float) -> bool:
        token_key = session.token_key or session.token_id
        allowed, reason = risk_check(
            token_key=token_key or "",
            order_shares=order_size,
            my_shares=session.open_size,
            ref_price=ref_price,
            cfg=self._risk_config,
            side="SELL",
            cumulative_total_usd=None,
            cumulative_token_usd=None,
        )
        self._log(
            "debug",
            (
                "[risk] SELL token_id=%s allowed=%s reason=%s order_size=%.6f open_size=%.6f "
                "ref_price=%.6f"
            )
            % (
                session.token_id,
                allowed,
                reason,
                order_size,
                session.open_size,
                ref_price,
            ),
        )
        if not allowed:
            self._log("info", f"[risk] SELL 拒绝 token_id={session.token_id} reason={reason}")
        return allowed

    def _record_cumulative_buy(self, session: MakerSession, filled: float, avg_price: float) -> None:
        token_key = session.token_key or session.token_id
        notional = filled * avg_price
        with self._state_lock:
            total = float(self._state.get("cumulative_buy_usd_total") or 0.0)
            total += notional
            self._state["cumulative_buy_usd_total"] = total
            per_token = self._state.get("cumulative_buy_usd_by_token")
            if not isinstance(per_token, dict):
                per_token = {}
            per_token[token_key] = float(per_token.get(token_key) or 0.0) + notional
            self._state["cumulative_buy_usd_by_token"] = per_token

    def _update_sell_only(self, session: MakerSession) -> None:
        if session.sell_only_start_ts is None:
            session.sell_only_start_ts = self._resolve_sell_only_start(session.token_id)
        if session.sell_only_start_ts and time.time() >= session.sell_only_start_ts:
            session.strategy.enable_sell_only("countdown window")

    def _resolve_sell_only_start(self, token_id: str) -> Optional[float]:
        countdown_cfg = self._config.get("countdown", {})
        if not isinstance(countdown_cfg, dict):
            return None
        absolute = countdown_cfg.get("absolute_time") or countdown_cfg.get("timestamp")
        if absolute:
            return _parse_timestamp(absolute, countdown_cfg.get("timezone"))
        minutes_before = countdown_cfg.get("minutes_before_end")
        if minutes_before is None:
            return None
        market_meta = gamma_fetch_markets_by_clob_token_ids([token_id]).get(token_id)
        if not market_meta or market_tradeable_state(market_meta) is False:
            return None
        end_ts = market_meta.get("endDate") or market_meta.get("endTime")
        end_ts_val = _parse_timestamp(end_ts, countdown_cfg.get("timezone"))
        if not end_ts_val:
            return None
        try:
            minutes_before = float(minutes_before)
        except (TypeError, ValueError):
            return None
        return end_ts_val - minutes_before * 60.0

    def _record_error(self, token_id: str, message: str) -> None:
        with self._lock:
            session = self._sessions.get(token_id)
            if session:
                session.errors.append(message)
        self._log("error", f"[maker] token_id={token_id} {message}")

    def _cap_buy_order_size(
        self,
        session: MakerSession,
        order_size: float,
        ref_price: float,
    ) -> float:
        if ref_price <= 0:
            return order_size
        max_per_token = _float_or_none(self._risk_config.get("max_notional_per_token"))
        max_total = _float_or_none(self._risk_config.get("max_notional_total"))
        with self._state_lock:
            total_usd = float(self._state.get("cumulative_buy_usd_total") or 0.0)
            token_usd_map = self._state.get("cumulative_buy_usd_by_token") or {}
            token_key = session.token_key or session.token_id
            token_usd = float(token_usd_map.get(token_key) or 0.0)

        caps: list[float] = []
        if max_per_token is not None:
            remaining = max_per_token - token_usd
            caps.append(max(remaining / ref_price, 0.0))
        if max_total is not None:
            remaining = max_total - total_usd
            caps.append(max(remaining / ref_price, 0.0))
        if not caps:
            return order_size
        capped = min([order_size, *caps])
        if capped < order_size:
            self._log(
                "info",
                (
                    "[risk] BUY 限额裁剪 token_id=%s original=%.6f capped=%.6f "
                    "ref_price=%.6f"
                )
                % (session.token_id, order_size, capped, ref_price),
            )
        return capped

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            print(message)
            return
        log_fn = getattr(self._logger, level, None)
        if callable(log_fn):
            log_fn(message)
        else:
            self._logger.info(message)


def _parse_timestamp(value: Any, tz_hint: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 1e12:
            return numeric / 1000.0
        return numeric
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            numeric = float(text)
        except ValueError:
            return None
        if numeric > 1e12:
            return numeric / 1000.0
        return numeric
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        if tz_hint:
            try:
                tz = ZoneInfo(str(tz_hint))
            except Exception:
                tz = timezone.utc
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
