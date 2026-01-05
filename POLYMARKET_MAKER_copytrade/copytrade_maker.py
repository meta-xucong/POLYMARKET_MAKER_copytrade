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

from smartmoney_query.poly_martmoney_query.api_client import DataApiClient

from modules.maker_engine import MakerEngine
from modules.position_manager import PositionManager
from modules.signal_tracker import SignalTracker
from modules.state_store import load_state, save_state


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

    logger.info("[init] copytrade_maker 启动成功")

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
                    allowed_topics.append(topic)
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
                cooldown_sec = float(cooldown_cfg.get("cooldown_sec_per_token") or 0)
                if cooldown_sec > 0 and bool(cooldown_cfg.get("exit_ignore_cooldown", True)):
                    for topic in event.topics:
                        if topic.token_id:
                            _mark_ignored(topic.token_id, cooldown_sec, "exit_cooldown")
        maker_engine.tick()
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
