from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

V2_ROOT = Path(__file__).resolve().parent
V1_COPYTRADE_ROOT = V2_ROOT / "POLYMARKET_MAKER_copytrade_v1"
LEGACY_COPYTRADE_ROOT = V2_ROOT.parent / "POLYMARKET_MAKER_copytrade"
AUTO_ROOT = V2_ROOT / "POLYMARKET_MAKER_AUTO"

if V1_COPYTRADE_ROOT.exists():
    COPYTRADE_ROOT = V1_COPYTRADE_ROOT
else:
    COPYTRADE_ROOT = LEGACY_COPYTRADE_ROOT

if str(COPYTRADE_ROOT) not in sys.path:
    sys.path.insert(0, str(COPYTRADE_ROOT))
if str(AUTO_ROOT) not in sys.path:
    sys.path.insert(0, str(AUTO_ROOT))
if str(LEGACY_COPYTRADE_ROOT) not in sys.path:
    sys.path.insert(0, str(LEGACY_COPYTRADE_ROOT))

from copytrade_runner import build_signal_tracker, events_to_topics, load_config, write_topics_snapshot
import poly_maker_autorun


def _split_topics_by_signal(topics: List[Dict[str, Any]]) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    buys: List[Dict[str, Any]] = []
    sells: List[Dict[str, Any]] = []
    for payload in topics:
        signal_type = str(payload.get("signal_type") or "").upper()
        if signal_type == "SELL":
            sells.append(payload)
        elif signal_type == "BUY":
            buys.append(payload)
    return buys, sells


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="copytrade -> maker orchestrator")
    parser.add_argument(
        "--copytrade-config",
        type=Path,
        default=COPYTRADE_ROOT / "config.json",
        help="copytrade 配置文件路径",
    )
    parser.add_argument(
        "--copytrade-output",
        type=Path,
        default=V2_ROOT / "data" / "copytrade_topics.json",
        help="copytrade 输出 topics JSON 路径",
    )
    parser.add_argument(
        "--global-config",
        type=Path,
        default=AUTO_ROOT / "POLYMARKET_MAKER" / "config" / "global_config.json",
        help="maker 调度全局配置 JSON 路径",
    )
    parser.add_argument(
        "--strategy-config",
        type=Path,
        default=AUTO_ROOT / "POLYMARKET_MAKER" / "config" / "strategy_defaults.json",
        help="maker 策略参数模板 JSON 路径",
    )
    parser.add_argument(
        "--run-config-template",
        type=Path,
        default=AUTO_ROOT / "POLYMARKET_MAKER" / "config" / "run_params.json",
        help="运行参数模板 JSON 路径（传递给 Volatility_arbitrage_run.py）",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="仅轮询一次并退出",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    copytrade_cfg = load_config(args.copytrade_config)
    _, tracker, poll_interval = build_signal_tracker(copytrade_cfg)

    maker_args = argparse.Namespace(
        global_config=args.global_config,
        strategy_config=args.strategy_config,
        run_config_template=args.run_config_template,
    )
    global_conf, strategy_conf, run_params_template = poly_maker_autorun.load_configs(maker_args)
    manager = poly_maker_autorun.AutoRunManager(global_conf, strategy_conf, run_params_template)

    worker = threading.Thread(target=manager.run_loop, daemon=True)
    worker.start()

    stop_event = threading.Event()

    def _handle_sigterm(signum: int, frame: Any) -> None:
        stop_event.set()
        manager.stop_event.set()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    while not stop_event.is_set():
        events = tracker.poll()
        topics = events_to_topics(events)
        if topics:
            write_topics_snapshot(args.copytrade_output, topics)
            buys, sells = _split_topics_by_signal(topics)
            if buys:
                manager.start_topics(buys)
            if sells:
                manager.stop_topics(sells)
        if args.once:
            break
        time.sleep(poll_interval)

    manager.stop_event.set()
    worker.join()


if __name__ == "__main__":
    main()
