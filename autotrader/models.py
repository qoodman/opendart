"""공통 데이터 모델: 종목, 주문, 체결, 포지션, 봉."""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from enum import Enum


class Market(str, Enum):
    KRX_STOCK = "KRX_STOCK"  # 국내 주식 (코스피·코스닥)
    KRX_ETF = "KRX_ETF"      # 국내 ETF (매도 시 거래세 면제)
    US = "US"                # 미국 주식·ETF

    @property
    def currency(self) -> str:
        return "USD" if self is Market.US else "KRW"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    NEW = "NEW"              # 만들었지만 아직 전송 전
    SENT = "SENT"            # 증권사 접수
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"

    @property
    def is_open(self) -> bool:
        return self in (OrderStatus.NEW, OrderStatus.SENT, OrderStatus.PARTIAL)


# 미국 거래소 코드: 주문용 / 시세용 / 실시간용 접두어
US_EXCHANGES = {
    "NASD": ("NASD", "NAS", "DNAS"),
    "NYSE": ("NYSE", "NYS", "DNYS"),
    "AMEX": ("AMEX", "AMS", "DAMS"),
}


@dataclass(frozen=True)
class Instrument:
    symbol: str                 # 국내: 6자리 코드(005930), 미국: 티커(AAPL)
    market: Market
    exchange: str = ""          # 미국만 사용: NASD / NYSE / AMEX

    def __post_init__(self) -> None:
        if self.market is Market.US and self.exchange not in US_EXCHANGES:
            raise ValueError(f"미국 종목은 exchange를 NASD/NYSE/AMEX 중 하나로 지정하세요: {self.symbol}")

    @property
    def currency(self) -> str:
        return self.market.currency

    @property
    def key(self) -> str:
        return f"{self.market.value}:{self.symbol}"


@dataclass(frozen=True)
class Bar:
    instrument: Instrument
    ts: float          # 봉 시작 시각 (epoch 초)
    open: float
    high: float
    low: float
    close: float
    volume: float


_order_seq = itertools.count(1)


@dataclass
class Order:
    instrument: Instrument
    side: Side
    qty: int
    price: float                 # 기준 가격(봉 종가 등). 증권사 전송 가격은 브로커가 정함
    reason: str = ""
    client_id: int = field(default_factory=lambda: next(_order_seq))
    status: OrderStatus = OrderStatus.NEW
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    fees: float = 0.0
    broker_order_id: str = ""
    broker_org_no: str = ""      # 국내 정정·취소에 필요한 주문조직번호
    created_ts: float = field(default_factory=time.time)
    reject_reason: str = ""

    @property
    def remaining(self) -> int:
        return self.qty - self.filled_qty

    @property
    def notional(self) -> float:
        return self.qty * self.price


@dataclass
class Position:
    instrument: Instrument
    qty: int = 0
    avg_price: float = 0.0       # 수수료를 뺀 순수 평균단가
    realized_pnl: float = 0.0    # 수수료·세금 차감 후 실현손익 (해당 통화)
    fees: float = 0.0            # 누적 수수료·세금
    entry_ts: float = 0.0

    def unrealized(self, mark: float) -> float:
        return (mark - self.avg_price) * self.qty
