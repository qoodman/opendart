"""전략 인터페이스.

전략은 봉(Bar)을 받아 Signal 목록을 돌려주기만 합니다. 주문 전송, 리스크,
수수료 계산은 엔진이 맡으므로 같은 전략이 백테스트·모의·실전에서 그대로 돕니다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..fees import FeeModel
from ..models import Bar, Instrument, Position, Side


@dataclass
class Signal:
    instrument: Instrument
    side: Side
    qty: int
    price: float                       # 기준 가격 (보통 봉 종가)
    expected_edge_rate: float | None = None  # 진입 시 기대 수익률. 청산이면 None
    reason: str = ""


@dataclass
class StrategyContext:
    """전략이 볼 수 있는 계좌 상태 (읽기 전용으로 쓰세요)."""
    fee_model: FeeModel
    position: Position
    pending_buy: int = 0
    pending_sell: int = 0
    krw_per_unit: float = 1.0          # 종목 통화 1단위의 원화 가치 (미국 = 환율)


class Strategy(Protocol):
    name: str

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[Signal]: ...
