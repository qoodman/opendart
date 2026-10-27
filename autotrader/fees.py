"""수수료·세금·호가단위 모델.

기본값은 2026년 기준 대략적인 수치입니다. 증권사 이벤트, 세법 개정에 따라
달라지므로 실제 계좌 조건에 맞게 config 에서 덮어쓰세요.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .models import Instrument, Market, Side


@dataclass(frozen=True)
class FeeSchedule:
    commission_rate: float = 0.0     # 매수·매도 각각 부과 (거래대금 대비)
    min_commission: float = 0.0      # 건당 최소 수수료
    sell_tax_rate: float = 0.0       # 매도 시 세금 (국내: 증권거래세+농특세)
    sec_fee_rate: float = 0.0        # 미국 매도 SEC fee (거래대금 대비)
    taf_per_share: float = 0.0       # 미국 매도 FINRA TAF (주당)
    taf_max: float = 0.0             # TAF 건당 상한
    fx_spread_rate: float = 0.0      # 환전 스프레드 (매수·매도 각각). 달러를 계속 보유하면 0

    def fee(self, side: Side, qty: int, price: float) -> float:
        """한 번의 체결에 대한 수수료+세금 (해당 통화 단위)."""
        if qty <= 0:
            return 0.0
        amount = qty * price
        total = max(amount * self.commission_rate, self.min_commission if self.commission_rate else 0.0)
        total += amount * self.fx_spread_rate
        if side is Side.SELL:
            total += amount * self.sell_tax_rate
            total += amount * self.sec_fee_rate
            if self.taf_per_share:
                taf = qty * self.taf_per_share
                total += min(taf, self.taf_max) if self.taf_max else taf
        return total


# 기본 수수료표 (실제 계좌 조건으로 바꿔 쓰세요)
DEFAULT_FEES: dict[Market, FeeSchedule] = {
    # 한국투자 비대면 계좌 수준 수수료 + 2026년 증권거래세(코스피·코스닥 합산 약 0.20%)
    Market.KRX_STOCK: FeeSchedule(commission_rate=0.00015, sell_tax_rate=0.0020),
    # 국내 ETF 는 매도 거래세 면제
    Market.KRX_ETF: FeeSchedule(commission_rate=0.00015, sell_tax_rate=0.0),
    # 미국: 기본 온라인 수수료 0.25%, 매도 시 SEC fee·FINRA TAF
    Market.US: FeeSchedule(
        commission_rate=0.0025,
        sec_fee_rate=0.0000278,
        taf_per_share=0.000166,
        taf_max=8.30,
    ),
}


def tick_size(market: Market, price: float) -> float:
    """호가 단위."""
    if market is Market.US:
        return 0.01 if price >= 1.0 else 0.0001
    if market is Market.KRX_ETF:
        return 1.0 if price < 2000 else 5.0
    # 국내 주식 (2023년 코스피·코스닥 통일 호가단위)
    for limit, tick in ((2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)):
        if price < limit:
            return float(tick)
    return 1000.0


def round_to_tick(market: Market, price: float, side: Side) -> float:
    """주문 가능한 가격으로 맞춤. 매수는 올림, 매도는 내림 (체결 잘 되는 쪽)."""
    tick = tick_size(market, price)
    steps = price / tick
    steps = math.ceil(steps - 1e-9) if side is Side.BUY else math.floor(steps + 1e-9)
    value = steps * tick
    return round(value, 4) if market is Market.US else float(int(round(value)))


class FeeModel:
    """시장별 수수료표 + 슬리피지로 거래 비용을 계산."""

    def __init__(self, overrides: dict[Market, FeeSchedule] | None = None, slippage_ticks: float = 1.0):
        self.schedules = dict(DEFAULT_FEES)
        if overrides:
            self.schedules.update(overrides)
        self.slippage_ticks = slippage_ticks

    def schedule(self, market: Market) -> FeeSchedule:
        return self.schedules[market]

    def with_override(self, market: Market, **kwargs) -> "FeeModel":
        new = FeeModel(self.schedules, self.slippage_ticks)
        new.schedules[market] = replace(self.schedules[market], **kwargs)
        return new

    def fee(self, instrument: Instrument, side: Side, qty: int, price: float) -> float:
        return self.schedule(instrument.market).fee(side, qty, price)

    def round_trip_cost_rate(self, instrument: Instrument, price: float, qty: int = 1,
                             include_slippage: bool = True) -> float:
        """사고 팔 때 드는 총비용을 거래대금 대비 비율로 (예: 0.0025 = 0.25%).

        이 값보다 가격이 더 움직여야 본전입니다.
        """
        if price <= 0 or qty <= 0:
            raise ValueError("price, qty 는 0보다 커야 합니다")
        amount = qty * price
        cost = self.fee(instrument, Side.BUY, qty, price) + self.fee(instrument, Side.SELL, qty, price)
        if include_slippage:
            cost += 2 * self.slippage_ticks * tick_size(instrument.market, price) * qty
        return cost / amount
