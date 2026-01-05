from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MAKER_ROOT = REPO_ROOT / "POLYMARKET_MAKER_AUTO" / "POLYMARKET_MAKER"
if str(MAKER_ROOT) not in sys.path:
    sys.path.insert(0, str(MAKER_ROOT))

from smartmoney_query.api_client import DataApiClient

from copytrade_v3_muti.ct_data import fetch_positions_norm
from copytrade_v3_muti.ct_resolver import resolve_token_id
from modules.maker_engine import MakerEngine
from modules.position_manager import PositionManager
from modules.signal_tracker import SignalTracker
from modules.state_store import load_state, save_state
from modules.topic_selector import Topic


def _load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("配置文件必须是 JSON 对象")
    return payload


def _get_maker_client():
    ws_spec = importlib.util.find_spec("Volatility_arbitrage_main_ws")
    if ws_spec is not None:
        module = importlib.import_module("Volatility_arbitrage_main_ws")
        return module.get_client()
    module = importlib.import_module("Volatility_arbitrage_main_rest")
    return module.get_client()


def _setup_logging(cfg: Dict[str, Any], base_dir: Path) -> logging.Logger:
    log_cfg = cfg.get("logging", {}) if isinstance(cfg.get("logging"), dict) else {}
    level_name = str(log_cfg.get("level", "INFO")).upper()
    level = logging.INFO
    if level_name in logging._nameToLevel:
        level = logging._nameToLevel[level_name]
    log_path = log_cfg.get("path")

    logger = logging.getLogger("copytrade_maker")
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s")

    logger.handlers.clear()
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if not log_path:
        log_dir_value = cfg.get("log_dir") or log_cfg.get("dir") or "logs"
        log_dir = Path(log_dir_value)
        if not log_dir.is_absolute():
            log_dir = base_dir / log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        pid = os.getpid()
        log_file = log_dir / f"copytrade_maker_{timestamp}_pid{pid}.log"
    else:
        log_file = Path(log_path)
        if not log_file.is_absolute():
            log_file = base_dir / log_file
        log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def run_loop(cfg: Dict[str, Any], *, base_dir: Path, config_path: Path) -> None:
    logger = _setup_logging(cfg, base_dir)

    target_addresses = cfg.get("target_addresses")
    if target_addresses is None:
        accounts_cfg = cfg.get("accounts", {}) if isinstance(cfg.get("accounts"), dict) else {}
        target_addresses = accounts_cfg.get("target_accounts", [])
    if not target_addresses:
        raise ValueError("未配置 target_addresses")

    signal_cfg = cfg.get("signal_tracking", {}) if isinstance(cfg.get("signal_tracking"), dict) else {}
    poll_interval = float(signal_cfg.get("poll_interval_sec", 5))
    watch_min_position_usdc = float(signal_cfg.get("watch_position_min_usdc", 10.0))
    watch_poll_interval = float(
        signal_cfg.get("watchlist_poll_interval_sec", signal_cfg.get("position_poll_interval_sec", 20))
    )
    scheduler_cfg = cfg.get("scheduler", {}) if isinstance(cfg.get("scheduler"), dict) else {}
    risk_cfg = cfg.get("risk", {}) if isinstance(cfg.get("risk"), dict) else {}
    cooldown_cfg = cfg.get("cooldown", {}) if isinstance(cfg.get("cooldown"), dict) else {}
    state_cfg = cfg.get("state", {}) if isinstance(cfg.get("state"), dict) else {}
    orderbook_cfg = cfg.get("orderbook", {}) if isinstance(cfg.get("orderbook"), dict) else {}
    run_params = cfg.get("run_params", {}) if isinstance(cfg.get("run_params"), dict) else {}
    strategy_defaults = (
        cfg.get("maker_strategy_defaults", {})
        if isinstance(cfg.get("maker_strategy_defaults"), dict)
        else {}
    )

    data_client = DataApiClient()
    maker_client = _get_maker_client()

    state_path = state_cfg.get("path") or "state/copytrade_state.json"
    state_file = Path(state_path)
    if not state_file.is_absolute():
        state_file = base_dir / state_file
    state_lock = threading.Lock()
    state = load_state(str(state_file))

    signal_tracker = SignalTracker(
        data_client,
        target_addresses,
        poll_interval_sec=poll_interval,
        position_poll_interval_sec=float(signal_cfg.get("position_poll_interval_sec", 20)),
        position_size_threshold=float(signal_cfg.get("position_size_threshold", 0.0)),
        positions_refresh_sec=signal_cfg.get("positions_refresh_sec"),
        positions_cache_bust_mode=str(signal_cfg.get("positions_cache_bust_mode", "sec")),
        sell_confirm_max=int(signal_cfg.get("sell_confirm_max", 5)),
        sell_confirm_window_sec=int(signal_cfg.get("sell_confirm_window_sec", 300)),
        sell_confirm_force_ratio=float(signal_cfg.get("sell_confirm_force_ratio", 0.5)),
        sell_confirm_force_shares=float(signal_cfg.get("sell_confirm_force_shares", 0.0)),
        blacklist_token_keys=risk_cfg.get("blacklist_token_keys") or [],
        logger=logger,
    )
    maker_engine = MakerEngine(
        maker_client,
        cfg.get("maker_strategy", {}),
        run_params=run_params,
        strategy_defaults=strategy_defaults,
        risk_config=risk_cfg,
        scheduler_config=scheduler_cfg,
        orderbook_config=orderbook_cfg,
        state=state,
        state_lock=state_lock,
        logger=logger,
    )
    position_manager = PositionManager(
        maker_client,
        cfg.get("maker_strategy", {}),
        maker_engine,
        max_concurrent_exits=int(scheduler_cfg.get("max_concurrent_exit_jobs") or 0),
        logger=logger,
    )

    def _fetch_target_positions() -> tuple[dict[str, float], dict[str, float]]:
        by_key: dict[str, float] = {}
        by_id: dict[str, float] = {}
        token_cache: dict[str, str] = {}
        for account in target_addresses:
            try:
                positions, _ = fetch_positions_norm(
                    data_client,
                    account,
                    float(signal_cfg.get("position_size_threshold", 0.0)),
                    refresh_sec=signal_cfg.get("positions_refresh_sec"),
                    cache_bust_mode=str(signal_cfg.get("positions_cache_bust_mode", "sec")),
                )
            except Exception as exc:
                logger.warning("[bootstrap] fetch target positions failed account=%s: %s", account, exc)
                continue
            for pos in positions:
                if not isinstance(pos, dict):
                    continue
                token_key = pos.get("token_key")
                size = pos.get("size")
                try:
                    size_val = float(size or 0.0)
                except (TypeError, ValueError):
                    continue
                if size_val <= 0:
                    continue
                if token_key:
                    by_key[str(token_key)] = size_val
                token_id = pos.get("token_id") or pos.get("tokenId") or pos.get("asset")
                if not token_id and token_key:
                    try:
                        token_id = resolve_token_id(str(token_key), pos, token_cache)
                    except Exception:
                        token_id = None
                if token_id:
                    by_id[str(token_id)] = size_val
        return by_key, by_id

    def _bootstrap_existing_positions() -> None:
        snapshot = position_manager.fetch_positions_snapshot()
        if not snapshot:
            logger.info("[bootstrap] 未检测到历史仓位")
            return
        target_by_key, target_by_id = _fetch_target_positions()
        token_cache: dict[str, str] = {}
        logger.info(
            "[bootstrap] 检测到历史仓位=%s 目标仓位(token_key)=%s",
            len(snapshot),
            len(target_by_key),
        )
        for pos in snapshot:
            token_id = pos.get("token_id")
            token_key = pos.get("token_key")
            size = pos.get("size")
            condition_id = pos.get("condition_id")
            outcome_index = pos.get("outcome_index")
            slug = pos.get("slug")
            raw = pos.get("raw")
            try:
                size_val = float(size or 0.0)
            except (TypeError, ValueError):
                continue
            if size_val <= 0:
                continue
            hold_until_sell = False
            if token_key and token_key in target_by_key:
                hold_until_sell = True
            if token_id and token_id in target_by_id:
                hold_until_sell = True
            if not token_id and token_key:
                try:
                    token_id = resolve_token_id(
                        str(token_key),
                        {
                            "token_key": token_key,
                            "condition_id": condition_id,
                            "outcome_index": outcome_index,
                            "slug": slug,
                            "raw": raw if isinstance(raw, dict) else {},
                        },
                        token_cache,
                    )
                except Exception as exc:
                    logger.warning("[bootstrap] 无法解析 token_id token_key=%s: %s", token_key, exc)
                    continue
            topic = Topic(
                token_id=str(token_id) if token_id else None,
                token_key=str(token_key) if token_key else None,
                condition_id=None,
                outcome_index=None,
                price=None,
            )
            maker_engine.start_existing_position(
                topic,
                size_val,
                hold_until_sell_signal=hold_until_sell,
            )
            logger.info(
                "[bootstrap] 接管 token_id=%s token_key=%s size=%.6f hold_until_sell=%s",
                token_id,
                token_key,
                size_val,
                hold_until_sell,
            )

    logger.info("[init] copytrade_maker 启动成功")

    _bootstrap_existing_positions()

    last_config_reload = time.time()
    last_state_save = time.time()
    config_reload_sec = float(cfg.get("config_reload_sec") or 0)
    state_save_interval = float(state_cfg.get("save_interval_sec") or 30.0)

    def _reload_config(current_cfg: Dict[str, Any]) -> Dict[str, Any]:
        try:
            new_cfg = _load_config(config_path)
        except Exception as exc:  # pragma: no cover - reload shouldn't crash main loop
            logger.error("[config] reload failed: %s", exc)
            return current_cfg
        return new_cfg

    def _cleanup_ignored(now_ts: float) -> None:
        with state_lock:
            ignored = state.get("ignored_tokens", {})
            if not isinstance(ignored, dict):
                state["ignored_tokens"] = {}
                return
            expired = [key for key, meta in ignored.items() if _ignored_expired(meta, now_ts)]
            for key in expired:
                ignored.pop(key, None)

    def _ignored_expired(meta: object, now_ts: float) -> bool:
        if not isinstance(meta, dict):
            return True
        until = meta.get("until")
        try:
            until_ts = float(until)
        except (TypeError, ValueError):
            return True
        return now_ts >= until_ts

    def _is_ignored(token_id: str, now_ts: float) -> Tuple[bool, Optional[str]]:
        with state_lock:
            ignored = state.get("ignored_tokens", {})
            if not isinstance(ignored, dict):
                return False, None
            meta = ignored.get(token_id)
        if not meta or _ignored_expired(meta, now_ts):
            return False, None
        reason = meta.get("reason") if isinstance(meta, dict) else None
        return True, str(reason) if reason else None

    def _mark_ignored(token_id: str, seconds: float, reason: str) -> None:
        until_ts = time.time() + max(seconds, 0.0)
        with state_lock:
            ignored = state.setdefault("ignored_tokens", {})
            if not isinstance(ignored, dict):
                ignored = {}
                state["ignored_tokens"] = ignored
            ignored[token_id] = {"until": until_ts, "reason": reason}

    def _cooldown_until(token_id: str) -> float:
        with state_lock:
            cooldown_map = state.get("cooldown_until", {})
            if not isinstance(cooldown_map, dict):
                return 0.0
            try:
                return float(cooldown_map.get(token_id) or 0.0)
            except (TypeError, ValueError):
                return 0.0

    def _set_cooldown(token_id: str, seconds: float) -> None:
        until_ts = time.time() + max(seconds, 0.0)
        with state_lock:
            cooldown_map = state.setdefault("cooldown_until", {})
            if not isinstance(cooldown_map, dict):
                cooldown_map = {}
                state["cooldown_until"] = cooldown_map
            cooldown_map[token_id] = until_ts

    def _watchlist_key(account: str, topic: Topic) -> str:
        token_part = topic.token_id or topic.token_key or "unknown"
        return f"{account}:{token_part}"

    def _add_watchlist(account: str, topic: Topic, size_val: float, threshold: float) -> None:
        key = _watchlist_key(account, topic)
        meta = {
            "account": account,
            "token_id": topic.token_id,
            "token_key": topic.token_key,
            "condition_id": topic.condition_id,
            "outcome_index": topic.outcome_index,
            "size": size_val,
            "threshold": threshold,
            "first_seen": int(time.time()),
            "last_seen": int(time.time()),
        }
        with state_lock:
            watchlist = state.setdefault("watchlist", {})
            if not isinstance(watchlist, dict):
                watchlist = {}
                state["watchlist"] = watchlist
            watchlist[key] = meta
        logger.info(
            "[watchlist] add account=%s token_id=%s token_key=%s size=%.6f threshold=%.6f",
            account,
            topic.token_id,
            topic.token_key,
            size_val,
            threshold,
        )

    def _remove_watchlist(key: str, reason: str) -> None:
        with state_lock:
            watchlist = state.get("watchlist", {})
            if not isinstance(watchlist, dict):
                return
            meta = watchlist.pop(key, None)
        if meta:
            logger.info(
                "[watchlist] remove key=%s reason=%s token_id=%s token_key=%s",
                key,
                reason,
                meta.get("token_id"),
                meta.get("token_key"),
            )

    def _fetch_positions_map(account: str) -> tuple[dict[str, float], dict[str, float]]:
        by_key: dict[str, float] = {}
        by_id: dict[str, float] = {}
        token_cache: dict[str, str] = {}
        positions, _ = fetch_positions_norm(
            data_client,
            account,
            0.0,
            refresh_sec=signal_cfg.get("positions_refresh_sec"),
            cache_bust_mode=str(signal_cfg.get("positions_cache_bust_mode", "sec")),
        )
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            token_key = pos.get("token_key")
            size = pos.get("size")
            avg_price = pos.get("avg_price")
            try:
                size_val = float(size or 0.0)
            except (TypeError, ValueError):
                continue
            try:
                avg_price_val = float(avg_price) if avg_price is not None else 0.0
            except (TypeError, ValueError):
                avg_price_val = 0.0
            if avg_price_val > 0:
                value_val = size_val * avg_price_val
            else:
                value_val = -1.0
            if token_key:
                by_key[str(token_key)] = value_val
            token_id = pos.get("token_id") or pos.get("tokenId") or pos.get("asset")
            if not token_id and token_key:
                try:
                    token_id = resolve_token_id(str(token_key), pos, token_cache)
                except Exception:
                    token_id = None
            if token_id:
                by_id[str(token_id)] = value_val
        return by_key, by_id

    def _lookup_position_value(
        topic: Topic,
        by_key: dict[str, float],
        by_id: dict[str, float],
    ) -> Optional[float]:
        if topic.token_id and topic.token_id in by_id:
            return by_id.get(topic.token_id)
        if topic.token_key and topic.token_key in by_key:
            return by_key.get(topic.token_key)
        return None

    def _refresh_watchlist(now_ts: float) -> None:
        with state_lock:
            watchlist = state.get("watchlist", {})
            if not isinstance(watchlist, dict) or not watchlist:
                return
            entries = dict(watchlist)

        account_map: dict[str, tuple[dict[str, float], dict[str, float]]] = {}
        to_start: list[tuple[str, Topic, float]] = []
        to_remove: list[tuple[str, str]] = []

        for key, meta in entries.items():
            account = str(meta.get("account") or "")
            if not account:
                to_remove.append((key, "invalid_account"))
                continue
            if account not in account_map:
                try:
                    account_map[account] = _fetch_positions_map(account)
                except Exception as exc:
                    logger.warning("[watchlist] fetch positions failed account=%s: %s", account, exc)
                    continue
            by_key, by_id = account_map[account]
            token_id = meta.get("token_id")
            token_key = meta.get("token_key")
            topic = Topic(
                token_id=str(token_id) if token_id else None,
                token_key=str(token_key) if token_key else None,
                condition_id=meta.get("condition_id"),
                outcome_index=meta.get("outcome_index"),
                price=None,
            )
            value_val = _lookup_position_value(topic, by_key, by_id)
            if value_val is None:
                to_remove.append((key, "position_closed"))
                continue
            if value_val < 0:
                with state_lock:
                    watchlist = state.get("watchlist", {})
                    if isinstance(watchlist, dict) and key in watchlist:
                        watchlist[key]["size"] = 0.0
                        watchlist[key]["threshold"] = float(watch_min_position_usdc)
                        watchlist[key]["last_seen"] = int(now_ts)
                continue
            if value_val <= 0:
                to_remove.append((key, "position_closed"))
                continue
            threshold = float(watch_min_position_usdc)
            if value_val >= threshold:
                to_start.append((key, topic, value_val))
            else:
                with state_lock:
                    watchlist = state.get("watchlist", {})
                    if isinstance(watchlist, dict) and key in watchlist:
                        watchlist[key]["size"] = value_val
                        watchlist[key]["threshold"] = threshold
                        watchlist[key]["last_seen"] = int(now_ts)

        if to_remove:
            for key, reason in to_remove:
                _remove_watchlist(key, reason)

        if not to_start:
            return

        allowed_topics: list[Topic] = []
        for key, topic, size_val in to_start:
            token_id = topic.token_id
            if not token_id:
                _remove_watchlist(key, "missing_token_id")
                continue
            cooldown_until = _cooldown_until(token_id)
            if cooldown_until and now_ts < cooldown_until:
                logger.info(
                    "[watchlist] cooldown token_id=%s until=%s",
                    token_id,
                    int(cooldown_until),
                )
                continue
            ignored, reason = _is_ignored(token_id, now_ts)
            if ignored:
                logger.info(
                    "[watchlist] ignored token_id=%s reason=%s",
                    token_id,
                    reason or "cooldown",
                )
                continue
            allowed_topics.append(topic)
            _remove_watchlist(key, "threshold_reached")
            logger.info(
                "[watchlist] promote token_id=%s token_key=%s size=%.6f",
                topic.token_id,
                topic.token_key,
                size_val,
            )

        if allowed_topics:
            maker_engine.start_topics(allowed_topics)
            cooldown_sec = float(cooldown_cfg.get("cooldown_sec_per_token") or 0)
            if cooldown_sec > 0:
                for topic in allowed_topics:
                    if topic.token_id:
                        _set_cooldown(topic.token_id, cooldown_sec)

    while True:
        now_ts = time.time()
        if config_reload_sec > 0 and now_ts - last_config_reload >= config_reload_sec:
            cfg = _reload_config(cfg)
            last_config_reload = now_ts
            signal_cfg = (
                cfg.get("signal_tracking", {})
                if isinstance(cfg.get("signal_tracking"), dict)
                else {}
            )
            poll_interval = float(signal_cfg.get("poll_interval_sec", poll_interval))
            scheduler_cfg = (
                cfg.get("scheduler", {}) if isinstance(cfg.get("scheduler"), dict) else scheduler_cfg
            )
            risk_cfg = cfg.get("risk", {}) if isinstance(cfg.get("risk"), dict) else risk_cfg
            cooldown_cfg = (
                cfg.get("cooldown", {}) if isinstance(cfg.get("cooldown"), dict) else cooldown_cfg
            )
            orderbook_cfg = (
                cfg.get("orderbook", {})
                if isinstance(cfg.get("orderbook"), dict)
                else orderbook_cfg
            )
            run_params = cfg.get("run_params", {}) if isinstance(cfg.get("run_params"), dict) else run_params
            strategy_defaults = (
                cfg.get("maker_strategy_defaults", {})
                if isinstance(cfg.get("maker_strategy_defaults"), dict)
                else strategy_defaults
            )
            state_cfg = cfg.get("state", {}) if isinstance(cfg.get("state"), dict) else state_cfg
            state_save_interval = float(state_cfg.get("save_interval_sec") or state_save_interval)
            signal_tracker.update_config(
                poll_interval_sec=poll_interval,
                position_poll_interval_sec=signal_cfg.get("position_poll_interval_sec"),
                position_size_threshold=signal_cfg.get("position_size_threshold"),
                positions_refresh_sec=signal_cfg.get("positions_refresh_sec"),
                positions_cache_bust_mode=signal_cfg.get("positions_cache_bust_mode"),
                sell_confirm_max=signal_cfg.get("sell_confirm_max"),
                sell_confirm_window_sec=signal_cfg.get("sell_confirm_window_sec"),
                sell_confirm_force_ratio=signal_cfg.get("sell_confirm_force_ratio"),
                sell_confirm_force_shares=signal_cfg.get("sell_confirm_force_shares"),
                blacklist_token_keys=risk_cfg.get("blacklist_token_keys") or [],
            )
            maker_engine.update_config(
                cfg.get("maker_strategy", {}),
                run_params=run_params,
                strategy_defaults=strategy_defaults,
                risk_config=risk_cfg,
                scheduler_config=scheduler_cfg,
                orderbook_config=orderbook_cfg,
            )
            position_manager.update_config(
                cfg.get("maker_strategy", {}),
                max_concurrent_exits=int(scheduler_cfg.get("max_concurrent_exit_jobs") or 0),
            )
            config_reload_sec = float(cfg.get("config_reload_sec") or config_reload_sec)
            watch_min_position_usdc = float(signal_cfg.get("watch_position_min_usdc", watch_min_position_usdc))
            watch_poll_interval = float(
                signal_cfg.get(
                    "watchlist_poll_interval_sec",
                    signal_cfg.get("position_poll_interval_sec", watch_poll_interval),
                )
            )

        _cleanup_ignored(now_ts)
        events = signal_tracker.poll()
        for event in events:
            if event.signal_type == "BUY":
                logger.info(
                    "[signal] BUY source=%s topics=%s",
                    event.source_account,
                    ",".join([t.identifier for t in event.topics]),
                )
                allowed_topics = []
                account_positions = None
                if watch_min_position_usdc > 0:
                    try:
                        account_positions = _fetch_positions_map(event.source_account)
                    except Exception as exc:
                        logger.warning(
                            "[signal] fetch positions failed account=%s: %s",
                            event.source_account,
                            exc,
                        )
                for topic in event.topics:
                    token_id = topic.token_id
                    if not token_id:
                        continue
                    cooldown_until = _cooldown_until(token_id)
                    if cooldown_until and now_ts < cooldown_until:
                        logger.info(
                            "[signal] BUY 冷却中 token_id=%s until=%s",
                            token_id,
                            int(cooldown_until),
                        )
                        continue
                    ignored, reason = _is_ignored(token_id, now_ts)
                    if ignored:
                        logger.info(
                            "[signal] BUY 忽略 token_id=%s reason=%s", token_id, reason or "cooldown"
                        )
                        continue
                    if account_positions is None:
                        _add_watchlist(
                            event.source_account,
                            topic,
                            0.0,
                            watch_min_position_usdc,
                        )
                        logger.info(
                            "[signal] BUY 待观察(仓位未获取) token_id=%s token_key=%s threshold=%.6f",
                            topic.token_id,
                            topic.token_key,
                            watch_min_position_usdc,
                        )
                        continue
                    by_key, by_id = account_positions
                    size_val = _lookup_position_value(topic, by_key, by_id)
                    if size_val is None:
                        size_for_watch = 0.0
                        _add_watchlist(
                            event.source_account,
                            topic,
                            size_for_watch,
                            watch_min_position_usdc,
                        )
                        logger.info(
                            "[signal] BUY 待观察(未找到仓位) token_id=%s token_key=%s threshold=%.6f",
                            topic.token_id,
                            topic.token_key,
                            watch_min_position_usdc,
                        )
                        continue
                    if size_val < 0:
                        _add_watchlist(
                            event.source_account,
                            topic,
                            0.0,
                            watch_min_position_usdc,
                        )
                        logger.info(
                            "[signal] BUY 待观察(价格缺失) token_id=%s token_key=%s threshold=%.6f",
                            topic.token_id,
                            topic.token_key,
                            watch_min_position_usdc,
                        )
                        continue
                    if size_val < watch_min_position_usdc:
                        size_for_watch = size_val
                        _add_watchlist(
                            event.source_account,
                            topic,
                            size_for_watch,
                            watch_min_position_usdc,
                        )
                        logger.info(
                            "[signal] BUY 待观察 token_id=%s token_key=%s size=%.6f threshold=%.6f",
                            topic.token_id,
                            topic.token_key,
                            size_for_watch,
                            watch_min_position_usdc,
                        )
                        continue
                    allowed_topics.append(topic)
                if allowed_topics:
                    for topic in allowed_topics:
                        _remove_watchlist(_watchlist_key(event.source_account, topic), "order_started")
                maker_engine.start_topics(allowed_topics)
                cooldown_sec = float(cooldown_cfg.get("cooldown_sec_per_token") or 0)
                if cooldown_sec > 0:
                    for topic in allowed_topics:
                        if topic.token_id:
                            _set_cooldown(topic.token_id, cooldown_sec)
            elif event.signal_type == "SELL":
                logger.info(
                    "[signal] SELL source=%s topics=%s",
                    event.source_account,
                    ",".join([t.identifier for t in event.topics]),
                )
                maker_engine.stop_topics(event.topics)
                position_manager.close_positions(event.topics)
                for topic in event.topics:
                    _remove_watchlist(_watchlist_key(event.source_account, topic), "sell_signal")
                cooldown_sec = float(cooldown_cfg.get("cooldown_sec_per_token") or 0)
                if cooldown_sec > 0 and bool(cooldown_cfg.get("exit_ignore_cooldown", True)):
                    for topic in event.topics:
                        if topic.token_id:
                            _mark_ignored(topic.token_id, cooldown_sec, "exit_cooldown")
        maker_engine.tick()
        if watch_poll_interval > 0:
            with state_lock:
                last_watch = state.get("last_watchlist_poll", 0.0) if isinstance(state, dict) else 0.0
            try:
                last_watch_ts = float(last_watch or 0.0)
            except (TypeError, ValueError):
                last_watch_ts = 0.0
            if now_ts - last_watch_ts >= watch_poll_interval:
                _refresh_watchlist(now_ts)
                with state_lock:
                    state["last_watchlist_poll"] = now_ts
        if state_save_interval > 0 and now_ts - last_state_save >= state_save_interval:
            with state_lock:
                save_state(str(state_file), state)
            last_state_save = now_ts
        sleep_sec = float(scheduler_cfg.get("poll_interval_seconds") or signal_tracker.poll_interval_sec)
        time.sleep(sleep_sec)


def main() -> None:
    parser = argparse.ArgumentParser(description="copytrade maker wave strategy")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("config.json")),
        help="配置文件路径",
    )
    args = parser.parse_args()

    cfg = _load_config(Path(args.config))
    config_path = Path(args.config)
    run_loop(cfg, base_dir=config_path.parent, config_path=config_path)


if __name__ == "__main__":
    main()
