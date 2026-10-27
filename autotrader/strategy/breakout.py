"""예시 전략: 수수료를 고려한 돌파 매매.

- 진입: 종가가 직전 N개 봉의 최고가를 넘으면 매수
- 청산: 목표 수익(take_profit), 손절(stop_loss), 최대 보유 봉 수 중 먼저 오는 것
- 목표 수익이 왕복 비용의 min_edge_multiple 배보다 작으면 아예 진입하지 않음

수익을 보장하는 전략이 아니라 구조 예시입니다. 반드시 백테스트로 검증하세요.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from ..models import Bar, Side
from .base import Signal, StrategyContext


@dataclass
class BreakoutParams:
    lookback: int = 20
    take_profit: float = 0.015      # +1.5%
    stop_loss: float = 0.008        # -0.8%
    max_hold_bars: int = 60
    order_value: float = 500_000    # 1회 진입 금액 (원). 미국 종목은 환율로 환산
    min_edge_multiple: float = 2.0


class CostAwareBreakout:
    name = "cost_aware_breakout"

    def __init__(self, params: BreakoutParams | None = None):
        self.p = params or BreakoutParams()
        self._highs: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.p.lookback))
        self._held_bars: dict[str, int] = defaultdict(int)

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[Signal]:
        key = bar.instrument.key
        highs = self._highs[key]
        pos = ctx.position
        signals: list[Signal] = []

        sellable = pos.qty - ctx.pending_sell
        if sellable > 0:
            self._held_bars[key] += 1
            change = bar.close / pos.avg_price - 1
            reason = ""
            if change >= self.p.take_profit:
                reason = f"익절 {change:+.2%}"
            elif change <= -self.p.stop_loss:
                reason = f"손절 {change:+.2%}"
            elif self._held_bars[key] >= self.p.max_hold_bars:
                reason = f"보유기간 만료 {change:+.2%}"
            if reason:
                signals.append(Signal(bar.instrument, Side.SELL, sellable, bar.close, None, reason))
        elif pos.qty == 0 and ctx.pending_buy == 0:
            self._held_bars[key] = 0
            if len(highs) == self.p.lookback and bar.close > max(highs):
                qty = int(self.p.order_value // (bar.close * ctx.krw_per_unit))
                if qty > 0:
                    cost = ctx.fee_model.round_trip_cost_rate(bar.instrument, bar.close, qty)
                    if self.p.take_profit >= cost * self.p.min_edge_multiple:
                        signals.append(Signal(
                            bar.instrument, Side.BUY, qty, bar.close,
                            expected_edge_rate=self.p.take_profit,
                            reason=f"{self.p.lookback}봉 고가 돌파 (왕복비용 {cost:.3%})",
                        ))

        highs.append(bar.high)
        return signals
