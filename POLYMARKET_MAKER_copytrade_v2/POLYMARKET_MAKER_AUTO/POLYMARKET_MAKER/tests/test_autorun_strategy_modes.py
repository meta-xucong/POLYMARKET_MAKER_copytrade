import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace(get=lambda *a, **k: None)

from poly_maker_autorun import AutoRunManager, GlobalConfig


def _build_manager(cfg: GlobalConfig) -> AutoRunManager:
    return AutoRunManager(cfg, strategy_defaults={}, run_params_template={})


def test_default_mode_is_classic():
    cfg = GlobalConfig.from_dict({})
    assert cfg.strategy_mode == "classic"
    manager = _build_manager(cfg)
    assert manager._is_aggressive_mode() is False
    assert manager._burst_slots() == 0


def test_aggressive_mode_uses_burst_slots_and_queue_promotion():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "strategy_mode": "aggressive",
                "aggressive_burst_slots": 3,
            }
        }
    )
    manager = _build_manager(cfg)
    assert manager._is_aggressive_mode() is True
    assert manager._burst_slots() == 3

    manager._enqueue_pending_topic("t1")
    manager._enqueue_burst_topic("t1", promote=True)
    assert "t1" not in manager.pending_topics
    assert manager.pending_burst_topics[0] == "t1"


def test_classic_mode_drains_burst_queue_into_pending():
    cfg = GlobalConfig.from_dict({"scheduler": {"strategy_mode": "classic"}})
    manager = _build_manager(cfg)
    manager.pending_burst_topics = ["x", "y"]
    manager.pending_topics = ["z"]

    manager._normalize_pending_queues_for_mode()

    assert manager.pending_burst_topics == []
    assert manager.pending_topics == ["z", "x", "y"]
