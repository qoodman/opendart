"""실시간 매매 엔진 (모의·실전 공통).

흐름: 웹소켓 체결가 → 1분봉 생성 → 전략 → 리스크 검사 → KIS 주문 → 체결통보 → OMS
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import aiohttp

from .config import KisSettings
from .fees import FeeModel, round_to_tick
from .kis import FillNotice, KisApiError, KisRestClient, KisWebSocket, Tick, TokenManager
from .models import Bar, Instrument, Market, Order, Side
from .oms import OrderManager
from .risk import RiskLimits, RiskManager
from .strategy.base import Strategy, StrategyContext

log = logging.getLogger(__name__)


class BarAggregator:
    """체결가 틱을 고정 간격 봉으로 묶음 (벽시계 기준)."""

    def __init__(self, interval_sec: int = 60):
        self.interval = interval_sec
        self._bars: dict[str, dict] = {}

    def on_tick(self, inst: Instrument, price: float, volume: float, now: float) -> list[Bar]:
        done = self.flush(now)
        bucket = int(now // self.interval) * self.interval
        b = self._bars.get(inst.key)
        if b is None:
            self._bars[inst.key] = {"inst": inst, "ts": bucket, "o": price, "h": price,
                                    "l": price, "c": price, "v": volume}
        else:
            b["h"], b["l"], b["c"] = max(b["h"], price), min(b["l"], price), price
            b["v"] += volume
        return done

    def flush(self, now: float) -> list[Bar]:
        """간격이 끝난 봉을 돌려줌."""
        done = []
        for key, b in list(self._bars.items()):
            if now >= b["ts"] + self.interval:
                done.append(Bar(b["inst"], b["ts"], b["o"], b["h"], b["l"], b["c"], b["v"]))
                del self._bars[key]
        return done


@dataclass
class EngineOptions:
    bar_seconds: int = 60
    order_timeout_sec: float = 30.0      # 미체결 주문 자동 취소
    us_limit_buffer: float = 0.005       # 미국 주문은 기준가 ±0.5% 지정가로 (체결 잘 되게)


class KisBroker:
    def __init__(self, rest: KisRestClient, opts: EngineOptions):
        self.rest = rest
        self.opts = opts

    async def place(self, order: Order) -> tuple[str, str]:
        inst = order.instrument
        if inst.market is Market.US:
            buf = 1 + self.opts.us_limit_buffer if order.side is Side.BUY else 1 - self.opts.us_limit_buffer
            price = round_to_tick(inst.market, order.price * buf, order.side)
            ack = await self.rest.us_order(inst.symbol, inst.exchange, order.side, order.qty, price)
        else:
            ack = await self.rest.domestic_order(inst.symbol, order.side, order.qty, None)  # 시장가
        return ack.order_no, ack.org_no

    async def cancel(self, order: Order) -> None:
        inst = order.instrument
        if inst.market is Market.US:
            await self.rest.us_cancel(inst.symbol, inst.exchange, order.broker_order_id, order.remaining)
        else:
            await self.rest.domestic_cancel(order.broker_order_id, order.broker_org_no)


class LiveEngine:
    def __init__(self, settings: KisSettings, instruments: list[Instrument], strategy: Strategy,
                 fee_model: FeeModel | None = None, limits: RiskLimits | None = None,
                 opts: EngineOptions | None = None, usdkrw: float = 1400.0):
        self.settings = settings
        self.instruments = instruments
        self.strategy = strategy
        self.fee_model = fee_model or FeeModel()
        self.opts = opts or EngineOptions()
        self.oms = OrderManager(self.fee_model, usdkrw=usdkrw)
        self.risk = RiskManager(limits or RiskLimits(), self.oms, self.fee_model)
        self.bars = BarAggregator(self.opts.bar_seconds)
        self._lock = asyncio.Lock()
        self.rest: KisRestClient | None = None
        self.broker: KisBroker | None = None
        self.ws: KisWebSocket | None = None

    async def run(self) -> None:
        mode = "모의투자" if self.settings.paper else "실전"
        log.info("엔진 시작 (%s), 종목 %s", mode, [i.symbol for i in self.instruments])
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            tokens = TokenManager(self.settings, session)
            self.rest = KisRestClient(self.settings, session, tokens)
            self.broker = KisBroker(self.rest, self.opts)
            await self._reconcile()
            self.ws = KisWebSocket(self.settings, session, tokens, self.instruments,
                                   self.on_tick, self.on_notice)
            housekeeping = asyncio.create_task(self._housekeeping())
            try:
                await self.ws.run()
            finally:
                housekeeping.cancel()
                await self._cancel_all("엔진 종료")

    async def _reconcile(self) -> None:
        """시작 시 국내 잔고를 읽어 포지션을 맞춤 (재시작해도 보유분을 이어서 관리)."""
        assert self.rest
        wanted = {i.symbol: i for i in self.instruments if i.market is not Market.US}
        try:
            rows = await self.rest.domestic_balance() if wanted else []
        except KisApiError as e:
            log.error("잔고 조회 실패: %s", e)
            return
        for r in rows:
            inst = wanted.get(r.get("pdno", ""))
            if inst:
                pos = self.oms.position(inst)
                pos.qty = int(r["hldg_qty"])
                pos.avg_price = float(r["pchs_avg_pric"])
                pos.entry_ts = time.time()
                log.info("기존 보유 %s %d주 @%s", inst.symbol, pos.qty, pos.avg_price)

    async def on_tick(self, tick: Tick) -> None:
        self.oms.update_mark(tick.instrument, tick.price)
        for bar in self.bars.on_tick(tick.instrument, tick.price, tick.volume, time.time()):
            await self._on_bar(bar)

    async def _on_bar(self, bar: Bar) -> None:
        async with self._lock:
            inst = bar.instrument
            ctx = StrategyContext(self.fee_model, self.oms.position(inst),
                                  self.oms.pending_qty(inst, Side.BUY), self.oms.pending_qty(inst, Side.SELL),
                                  self.oms.to_krw(1.0, inst.currency))
            for sig in self.strategy.on_bar(bar, ctx):
                order = self.oms.register(Order(sig.instrument, sig.side, sig.qty, sig.price, sig.reason))
                decision = self.risk.check(order, sig.expected_edge_rate)
                if not decision.ok:
                    self.oms.on_reject(order, decision.reason)
                    continue
                await self._send(order)

    async def _send(self, order: Order) -> None:
        assert self.broker
        try:
            order_no, org_no = await self.broker.place(order)
            self.oms.on_sent(order, order_no, org_no)
            log.info("주문 %s %s %d (%s) → %s", order.instrument.symbol, order.side.value,
                     order.qty, order.reason, order_no)
        except (KisApiError, aiohttp.ClientError, asyncio.TimeoutError) as e:
            # 타임아웃은 접수됐을 수도 있음 → 수동 확인 필요하므로 신규 진입 중단
            self.oms.on_reject(order, str(e))
            if isinstance(e, asyncio.TimeoutError):
                self.risk.kill("주문 응답 타임아웃 - 증권사 앱에서 주문 상태를 확인하세요")

    async def on_notice(self, n: FillNotice) -> None:
        async with self._lock:
            order = self.oms.find_by_broker_id(n.order_no)
            if order is None:
                log.info("엔진 밖 주문 통보 무시: %s %s", n.order_no, n.symbol)
                return
            if n.refused:
                self.oms.on_reject(order, "증권사 거부")
            elif n.is_fill and n.qty > 0:
                self.oms.on_fill(order, n.qty, n.price)
                self.risk.check_daily_loss()

    async def _housekeeping(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = time.time()
            for bar in self.bars.flush(now):
                await self._on_bar(bar)
            for order in self.oms.open_orders():
                if order.broker_order_id and now - order.created_ts > self.opts.order_timeout_sec:
                    await self._cancel(order, "미체결 시간 초과")
            if self.risk.killed:
                # 신규 매수만 취소. 청산 매도 주문은 살려 둠
                for order in self.oms.open_orders():
                    if order.side is Side.BUY and order.broker_order_id:
                        await self._cancel(order, self.risk.killed_reason)

    async def _cancel(self, order: Order, why: str) -> None:
        assert self.broker
        try:
            await self.broker.cancel(order)
            self.oms.on_cancel(order)
            log.info("취소 %s %s (%s)", order.instrument.symbol, order.broker_order_id, why)
        except KisApiError as e:
            log.error("취소 실패 %s: %s", order.broker_order_id, e)
            order.created_ts = time.time()  # 바로 재시도 폭주 방지

    async def _cancel_all(self, why: str) -> None:
        for order in self.oms.open_orders():
            if order.broker_order_id:
                await self._cancel(order, why)
