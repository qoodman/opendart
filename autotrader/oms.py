"""주문·포지션 관리 (OMS).

체결이 들어오면 포지션, 평균단가, 수수료 차감 실현손익을 갱신합니다.
실전·모의·백테스트가 모두 이 클래스를 같이 씁니다.
"""
from __future__ import annotations

import logging
import time

from .fees import FeeModel
from .models import Instrument, Order, OrderStatus, Position, Side

log = logging.getLogger(__name__)


class OrderManager:
    def __init__(self, fee_model: FeeModel, usdkrw: float = 1400.0):
        self.fee_model = fee_model
        self.usdkrw = usdkrw
        self.orders: dict[int, Order] = {}
        self.positions: dict[str, Position] = {}
        self.marks: dict[str, float] = {}
        self.trade_log: list[dict] = []

    # ---- 조회 ----
    def position(self, instrument: Instrument) -> Position:
        pos = self.positions.get(instrument.key)
        if pos is None:
            pos = self.positions[instrument.key] = Position(instrument)
        return pos

    def open_orders(self, instrument: Instrument | None = None) -> list[Order]:
        return [o for o in self.orders.values()
                if o.status.is_open and (instrument is None or o.instrument == instrument)]

    def pending_qty(self, instrument: Instrument, side: Side) -> int:
        return sum(o.remaining for o in self.open_orders(instrument) if o.side is side)

    def find_by_broker_id(self, broker_order_id: str) -> Order | None:
        for o in self.orders.values():
            if o.broker_order_id and o.broker_order_id.lstrip("0") == broker_order_id.lstrip("0"):
                return o
        return None

    def to_krw(self, amount: float, currency: str) -> float:
        return amount * self.usdkrw if currency == "USD" else amount

    def realized_pnl_krw(self) -> float:
        return sum(self.to_krw(p.realized_pnl, p.instrument.currency) for p in self.positions.values())

    def unrealized_pnl_krw(self) -> float:
        total = 0.0
        for p in self.positions.values():
            mark = self.marks.get(p.instrument.key)
            if p.qty and mark:
                total += self.to_krw(p.unrealized(mark), p.instrument.currency)
        return total

    def total_fees_krw(self) -> float:
        return sum(self.to_krw(p.fees, p.instrument.currency) for p in self.positions.values())

    def exposure_krw(self, instrument: Instrument | None = None) -> float:
        total = 0.0
        for p in self.positions.values():
            if instrument is not None and p.instrument != instrument:
                continue
            price = self.marks.get(p.instrument.key, p.avg_price)
            total += self.to_krw(abs(p.qty) * price, p.instrument.currency)
        return total

    # ---- 갱신 ----
    def update_mark(self, instrument: Instrument, price: float) -> None:
        self.marks[instrument.key] = price

    def register(self, order: Order) -> Order:
        self.orders[order.client_id] = order
        return order

    def on_sent(self, order: Order, broker_order_id: str, org_no: str = "") -> None:
        order.status = OrderStatus.SENT
        order.broker_order_id = broker_order_id
        order.broker_org_no = org_no

    def on_reject(self, order: Order, reason: str) -> None:
        order.status = OrderStatus.REJECTED
        order.reject_reason = reason
        log.warning("주문 거부 %s %s %s: %s", order.instrument.symbol, order.side.value, order.qty, reason)

    def on_cancel(self, order: Order) -> None:
        if order.status.is_open:
            order.status = OrderStatus.CANCELED

    def on_fill(self, order: Order, qty: int, price: float, ts: float | None = None) -> float:
        """체결 반영. 이번 체결의 수수료를 돌려줌."""
        qty = min(qty, order.remaining)
        if qty <= 0:
            return 0.0
        ts = ts if ts is not None else time.time()
        fee = self.fee_model.fee(order.instrument, order.side, qty, price)

        order.avg_fill_price = (order.avg_fill_price * order.filled_qty + price * qty) / (order.filled_qty + qty)
        order.filled_qty += qty
        order.fees += fee
        order.status = OrderStatus.FILLED if order.remaining == 0 else OrderStatus.PARTIAL

        pos = self.position(order.instrument)
        pos.fees += fee
        realized = 0.0
        if order.side is Side.BUY:
            if pos.qty == 0:
                pos.entry_ts = ts
            pos.avg_price = (pos.avg_price * pos.qty + price * qty) / (pos.qty + qty)
            pos.qty += qty
        else:
            close_qty = min(qty, pos.qty)
            realized = (price - pos.avg_price) * close_qty
            pos.qty -= qty
            if pos.qty <= 0:
                pos.avg_price = 0.0 if pos.qty == 0 else price
        pos.realized_pnl += realized - fee
        self.update_mark(order.instrument, price)

        self.trade_log.append({
            "ts": ts, "symbol": order.instrument.symbol, "market": order.instrument.market.value,
            "side": order.side.value, "qty": qty, "price": price, "fee": fee,
            "realized": realized, "reason": order.reason,
        })
        log.info("체결 %s %s %d@%s 수수료 %.2f", order.instrument.symbol, order.side.value, qty, price, fee)
        return fee
