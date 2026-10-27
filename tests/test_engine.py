"""실시간 엔진: 봉 → 신호 → 주문 → 체결통보 흐름 (가짜 브로커)."""
from autotrader.config import KisSettings
from autotrader.engine import LiveEngine
from autotrader.kis.ws import FillNotice
from autotrader.models import Bar, Instrument, Market, Side
from autotrader.risk import RiskLimits
from autotrader.strategy import BreakoutParams, CostAwareBreakout

SS = Instrument("005930", Market.KRX_STOCK)


class FakeBroker:
    def __init__(self):
        self.placed, self.canceled = [], []

    async def place(self, order):
        self.placed.append(order)
        return f"{len(self.placed):010d}", "06010"

    async def cancel(self, order):
        self.canceled.append(order)


async def test_engine_order_and_fill_flow():
    settings = KisSettings("K", "S", "12345678", "01", paper=True)
    strat = CostAwareBreakout(BreakoutParams(lookback=3, take_profit=0.015, order_value=600_000))
    engine = LiveEngine(settings, [SS], strat, limits=RiskLimits(kill_file=""))
    broker = FakeBroker()
    engine.broker = broker

    for i, p in enumerate([60000, 60000, 60000, 61000]):
        await engine._on_bar(Bar(SS, i * 60, p, p, p, p, 100))
    assert len(broker.placed) == 1 and broker.placed[0].side is Side.BUY
    qty = broker.placed[0].qty

    await engine.on_notice(FillNotice("1", "005930", Side.BUY, qty, 61000, is_fill=True, refused=False))
    assert engine.oms.position(SS).qty == qty

    # 익절 조건 → 매도 주문
    await engine._on_bar(Bar(SS, 300, 62000, 62000, 62000, 62000, 100))
    assert broker.placed[-1].side is Side.SELL and broker.placed[-1].qty == qty
