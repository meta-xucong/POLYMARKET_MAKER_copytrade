from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class ActionType(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class StrategyAction:
    action: ActionType


@dataclass
class StrategyConfig:
    token_id: str
    buy_price_threshold: Optional[float]
    drop_window_minutes: float
    drop_pct: float
    profit_pct: float
    enable_incremental_drop_pct: bool
    incremental_drop_pct_step: float
    incremental_drop_pct_cap: float
    disable_duplicate_signal: bool
    disable_sell_signals: bool
    min_price: Optional[float]
    max_price: Optional[float]
    min_market_order_size: Optional[float]


class VolArbStrategy:
    def __init__(self, cfg: StrategyConfig) -> None:
        self._cfg = cfg
        self._position_size = 0.0
        self._last_buy_price: Optional[float] = None
        self._last_sell_price: Optional[float] = None
        self._last_action: Optional[ActionType] = None
        self._sell_only = False
        self._stopped = False

    def sync_position(self, size: float) -> None:
        self._position_size = max(float(size), 0.0)

    def stop(self, _reason: str | None = None) -> None:
        self._stopped = True

    def resume(self) -> None:
        self._stopped = False

    def enable_sell_only(self, _reason: str | None = None) -> None:
        self._sell_only = True

    def on_tick(self, *, best_ask: float, best_bid: float, ts: float) -> Optional[StrategyAction]:
        if self._stopped:
            return None

        if self._position_size > 0:
            if self._cfg.disable_sell_signals and not self._sell_only:
                return None
            if self._sell_only:
                return StrategyAction(ActionType.SELL)
            if self._last_buy_price is None:
                return None
            target = self._last_buy_price * (1 + self._cfg.profit_pct)
            if best_bid >= target:
                return StrategyAction(ActionType.SELL)
            return None

        if self._sell_only:
            return None

        if self._cfg.min_price is not None and best_bid < self._cfg.min_price:
            return None
        if self._cfg.max_price is not None and best_bid > self._cfg.max_price:
            return None
        if self._cfg.buy_price_threshold is not None and best_bid > self._cfg.buy_price_threshold:
            return None

        if self._cfg.disable_duplicate_signal and self._last_action == ActionType.BUY:
            return None

        return StrategyAction(ActionType.BUY)

    def on_buy_filled(self, avg_price: float, *, size: float) -> None:
        self._position_size = max(float(size), 0.0)
        self._last_buy_price = float(avg_price)
        self._last_action = ActionType.BUY

    def on_sell_filled(
        self,
        *,
        avg_price: Optional[float] = None,
        size: Optional[float] = None,
        remaining: Optional[float] = None,
    ) -> None:
        if remaining is not None:
            self._position_size = max(float(remaining), 0.0)
        elif size is not None:
            self._position_size = max(self._position_size - float(size), 0.0)
        if avg_price is not None:
            self._last_sell_price = float(avg_price)
        if self._position_size <= 0:
            self._last_action = ActionType.SELL

    def on_reject(self, _reason: str) -> None:
        if self._cfg.disable_duplicate_signal:
            self._last_action = None

    def sell_trigger_price(self) -> Optional[float]:
        if self._last_buy_price is None:
            return None
        return self._last_buy_price * (1 + self._cfg.profit_pct)

