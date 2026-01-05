from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class Position:
    condition_id: str
    outcome_index: Optional[int]
    size: float
    avg_price: float
    slug: Optional[str]
    title: Optional[str]
    end_date: Optional[dt.datetime]
    raw: Dict[str, Any]


@dataclass
class Trade:
    side: Optional[str]
    size: float
    price: float
    timestamp: dt.datetime
    raw: Dict[str, Any]
    market_id: Optional[str] = None
