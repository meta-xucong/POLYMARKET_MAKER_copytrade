from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def risk_check(
    *,
    token_key: str,
    order_shares: float,
    my_shares: float,
    ref_price: float,
    cfg: Optional[Dict[str, Any]] = None,
    side: str = "BUY",
    cumulative_total_usd: Optional[float] = None,
    cumulative_token_usd: Optional[float] = None,
) -> Tuple[bool, str]:
    cfg = cfg or {}
    token_key = str(token_key or "")
    blacklist = cfg.get("blacklist_token_keys") or cfg.get("blacklist_tokens") or []
    if token_key and any(str(item).strip() == token_key for item in blacklist):
        return False, "blacklist_token"

    allow_short = bool(cfg.get("allow_short", False))
    if side.upper() == "SELL" and not allow_short and order_shares > max(my_shares, 0.0):
        return False, "short_not_allowed"

    notional = max(float(order_shares) * float(ref_price), 0.0)
    max_per_token = _float(cfg.get("max_notional_per_token"))
    if max_per_token is not None and cumulative_token_usd is not None:
        if cumulative_token_usd + notional > max_per_token:
            return False, "max_notional_per_token"
    max_total = _float(cfg.get("max_notional_total"))
    if max_total is not None and cumulative_total_usd is not None:
        if cumulative_total_usd + notional > max_total:
            return False, "max_notional_total"

    return True, ""

