from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


DEFAULT_STATE: Dict[str, Any] = {
    "ignored_tokens": {},
    "cooldown_until": {},
    "cumulative_buy_usd_total": 0.0,
    "cumulative_buy_usd_by_token": {},
}


def load_state(path: str) -> Dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return dict(DEFAULT_STATE)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_STATE)
    state = dict(DEFAULT_STATE)
    if isinstance(payload, dict):
        state.update(payload)
    if not isinstance(state.get("ignored_tokens"), dict):
        state["ignored_tokens"] = {}
    if not isinstance(state.get("cooldown_until"), dict):
        state["cooldown_until"] = {}
    if not isinstance(state.get("cumulative_buy_usd_total"), (int, float)):
        state["cumulative_buy_usd_total"] = 0.0
    if not isinstance(state.get("cumulative_buy_usd_by_token"), dict):
        state["cumulative_buy_usd_by_token"] = {}
    return state


def save_state(path: str, state: Dict[str, Any]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp_path.replace(file_path)
