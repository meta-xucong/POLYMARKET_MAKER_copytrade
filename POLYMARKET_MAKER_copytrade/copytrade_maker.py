from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict

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


def _setup_logging(cfg: Dict[str, Any]) -> logging.Logger:
    log_cfg = cfg.get("logging", {}) if isinstance(cfg.get("logging"), dict) else {}
    level = str(log_cfg.get("level", "INFO")).upper()
    log_path = log_cfg.get("path", "logs/app.log")

    logger = logging.getLogger("copytrade_maker")
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s")

    if not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        if log_path:
            log_file = Path(log_path)
            if not log_file.is_absolute():
                log_file = Path(__file__).resolve().parent / log_file
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    return logger


def run_loop(cfg: Dict[str, Any]) -> None:
    logger = _setup_logging(cfg)

    accounts_cfg = cfg.get("accounts", {}) if isinstance(cfg.get("accounts"), dict) else {}
    target_accounts = accounts_cfg.get("target_accounts", [])
    if not target_accounts:
        raise ValueError("未配置 target_accounts")

    signal_cfg = cfg.get("signal_tracking", {}) if isinstance(cfg.get("signal_tracking"), dict) else {}
    poll_interval = float(signal_cfg.get("poll_interval_sec", 5))

    data_client = DataApiClient()
    maker_client = _get_maker_client()

    signal_tracker = SignalTracker(
        data_client,
        target_accounts,
        poll_interval_sec=poll_interval,
        logger=logger,
    )
    maker_engine = MakerEngine(maker_client, cfg.get("maker_strategy", {}), logger=logger)
    position_manager = PositionManager(
        maker_client,
        cfg.get("maker_strategy", {}),
        maker_engine,
        logger=logger,
    )

    logger.info("[init] copytrade_maker 启动成功")

    while True:
        events = signal_tracker.poll()
        for event in events:
            if event.signal_type == "BUY":
                logger.info(
                    "[signal] BUY source=%s topics=%s",
                    event.source_account,
                    ",".join([t.identifier for t in event.topics]),
                )
                maker_engine.start_topics(event.topics)
            elif event.signal_type == "SELL":
                logger.info(
                    "[signal] SELL source=%s topics=%s",
                    event.source_account,
                    ",".join([t.identifier for t in event.topics]),
                )
                maker_engine.stop_topics(event.topics)
                position_manager.close_positions(event.topics)
        maker_engine.tick()
        time.sleep(signal_tracker.poll_interval_sec)


def main() -> None:
    parser = argparse.ArgumentParser(description="copytrade maker wave strategy")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("config.json")),
        help="配置文件路径",
    )
    args = parser.parse_args()

    cfg = _load_config(Path(args.config))
    run_loop(cfg)


if __name__ == "__main__":
    main()
