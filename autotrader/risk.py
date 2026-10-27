"""주문 전 리스크 검사와 킬 스위치.

모든 신규 주문은 여기서 통과해야 증권사로 나갑니다.
청산(포지션 줄이는 매도)은 손실 한도에 걸려도 허용합니다.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .fees import FeeModel
from .models import Order, Side
from .oms import OrderManager

log = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    max_order_krw: float = 1_000_000          # 주문 1건 최대 금액
    max_position_krw: float = 3_000_000       # 종목당 최대 보유 금액
    max_gross_exposure_krw: float = 10_000_000  # 전체 보유 금액 합계 상한
    max_daily_loss_krw: float = 300_000       # 하루 손실 한도 (실현+평가, 수수료 포함)
    max_orders_per_minute: int = 20
    min_edge_multiple: float = 2.0            # 기대수익이 왕복비용의 몇 배 이상이어야 진입할지
    kill_file: str = "KILL"                   # 이 파일이 생기면 즉시 매매 중단


@dataclass
class RiskDecision:
    ok: bool
    reason: str = ""


class RiskManager:
    def __init__(self, limits: RiskLimits, oms: OrderManager, fee_model: FeeModel):
        self.limits = limits
        self.oms = oms
        self.fee_model = fee_model
        self._order_times: deque[float] = deque()
        self._killed_reason = ""
        self._day_start_pnl = 0.0

    # ---- 킬 스위치 ----
    def kill(self, reason: str) -> None:
        if not self._killed_reason:
            log.error("킬 스위치 작동: %s", reason)
        self._killed_reason = reason

    @property
    def killed(self) -> bool:
        if not self._killed_reason and self.limits.kill_file and Path(self.limits.kill_file).exists():
            self.kill(f"{self.limits.kill_file} 파일 감지")
        return bool(self._killed_reason)

    @property
    def killed_reason(self) -> str:
        return self._killed_reason

    def total_pnl_krw(self) -> float:
        return self.oms.realized_pnl_krw() + self.oms.unrealized_pnl_krw()

    def daily_pnl_krw(self) -> float:
        return self.total_pnl_krw() - self._day_start_pnl

    def reset_day(self) -> None:
        """새 거래일 시작. 손실 한도 기준점을 다시 잡고, 손실 한도로 걸린 킬 스위치만 풉니다."""
        self._day_start_pnl = self.total_pnl_krw()
        if self._killed_reason.startswith("일일 손실"):
            self._killed_reason = ""

    def check_daily_loss(self) -> None:
        pnl = self.daily_pnl_krw()
        if pnl <= -self.limits.max_daily_loss_krw:
            self.kill(f"일일 손실 한도 도달 ({pnl:,.0f}원)")

    # ---- 주문 검사 ----
    def check(self, order: Order, expected_edge_rate: float | None = None,
              now: float | None = None) -> RiskDecision:
        now = now if now is not None else time.time()
        inst = order.instrument
        pos = self.oms.position(inst)
        # 검사 대상 주문 자신은 미체결 수량에서 뺌 (이미 OMS 에 등록됐을 수 있음)
        others = [o for o in self.oms.open_orders(inst) if o.client_id != order.client_id]
        pending_sell = sum(o.remaining for o in others if o.side is Side.SELL)
        pending_buy = sum(o.remaining for o in others if o.side is Side.BUY)
        reducing = order.side is Side.SELL and order.qty <= pos.qty - pending_sell

        if order.qty <= 0 or order.price <= 0:
            return RiskDecision(False, "수량·가격이 0 이하")

        if order.side is Side.SELL and not reducing:
            return RiskDecision(False, "보유 수량 초과 매도 (공매도 미지원)")

        # 주문 속도 제한 (청산 포함)
        while self._order_times and now - self._order_times[0] > 60:
            self._order_times.popleft()
        if len(self._order_times) >= self.limits.max_orders_per_minute:
            return RiskDecision(False, "분당 주문 수 한도")

        if not reducing:
            self.check_daily_loss()
            if self.killed:
                return RiskDecision(False, f"킬 스위치: {self._killed_reason}")

            order_krw = self.oms.to_krw(order.notional, inst.currency)
            if order_krw > self.limits.max_order_krw:
                return RiskDecision(False, f"주문 금액 한도 초과 ({order_krw:,.0f}원)")

            pending_buy_krw = self.oms.to_krw(pending_buy * order.price, inst.currency)
            if self.oms.exposure_krw(inst) + pending_buy_krw + order_krw > self.limits.max_position_krw:
                return RiskDecision(False, "종목당 보유 한도 초과")
            if self.oms.exposure_krw() + order_krw > self.limits.max_gross_exposure_krw:
                return RiskDecision(False, "전체 보유 한도 초과")

            # 수수료 검사: 기대수익이 왕복비용을 충분히 넘는지
            cost = self.fee_model.round_trip_cost_rate(inst, order.price, order.qty)
            if expected_edge_rate is None:
                return RiskDecision(False, "기대수익(expected_edge_rate) 미지정 진입")
            if expected_edge_rate < cost * self.limits.min_edge_multiple:
                return RiskDecision(
                    False,
                    f"기대수익 {expected_edge_rate:.3%} < 왕복비용 {cost:.3%} x {self.limits.min_edge_multiple}",
                )

        self._order_times.append(now)
        return RiskDecision(True)
