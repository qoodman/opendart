import base64
import time

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from autotrader.backtest import Backtester
from autotrader.engine import BarAggregator
from autotrader.fees import FeeModel, round_to_tick, tick_size
from autotrader.kis.ws import DOM_NOTICE_IDX, aes_decrypt, parse_notice, split_records
from autotrader.models import Bar, Instrument, Market, Order, Side
from autotrader.oms import OrderManager
from autotrader.risk import RiskLimits, RiskManager
from autotrader.strategy import BreakoutParams, CostAwareBreakout

SS = Instrument("005930", Market.KRX_STOCK)
ETF = Instrument("069500", Market.KRX_ETF)
AAPL = Instrument("AAPL", Market.US, "NASD")


# ---------- 수수료 ----------
def test_domestic_sell_includes_tax():
    fm = FeeModel()
    buy = fm.fee(SS, Side.BUY, 10, 60000)
    sell = fm.fee(SS, Side.SELL, 10, 60000)
    assert buy == pytest.approx(600000 * 0.00015)
    assert sell == pytest.approx(600000 * (0.00015 + 0.0020))


def test_etf_has_no_sell_tax():
    fm = FeeModel()
    assert fm.fee(ETF, Side.SELL, 10, 40000) == pytest.approx(fm.fee(ETF, Side.BUY, 10, 40000))


def test_us_sell_fees_and_taf_cap():
    fm = FeeModel()
    fee = fm.fee(AAPL, Side.SELL, 100_000, 1.0)
    expected = 100_000 * (0.0025 + 0.0000278) + 8.30
    assert fee == pytest.approx(expected)


def test_round_trip_cost_and_override():
    fm = FeeModel(slippage_ticks=0)
    assert fm.round_trip_cost_rate(SS, 60000, 10) == pytest.approx(0.00015 * 2 + 0.0020)
    cheap = fm.with_override(Market.US, commission_rate=0.0007)
    assert cheap.round_trip_cost_rate(AAPL, 200, 10) < fm.round_trip_cost_rate(AAPL, 200, 10)


@pytest.mark.parametrize("price,tick", [(1500, 1), (4990, 5), (15000, 10), (60000, 100), (300000, 500), (700000, 1000)])
def test_krx_tick_size(price, tick):
    assert tick_size(Market.KRX_STOCK, price) == tick


def test_round_to_tick_direction():
    assert round_to_tick(Market.KRX_STOCK, 60050, Side.BUY) == 60100
    assert round_to_tick(Market.KRX_STOCK, 60050, Side.SELL) == 60000
    assert round_to_tick(Market.US, 230.123, Side.BUY) == 230.13


# ---------- OMS ----------
def test_oms_realized_pnl_net_of_fees():
    fm = FeeModel()
    oms = OrderManager(fm)
    buy = oms.register(Order(SS, Side.BUY, 10, 60000))
    oms.on_fill(buy, 10, 60000)
    sell = oms.register(Order(SS, Side.SELL, 10, 61000))
    oms.on_fill(sell, 4, 61000)
    oms.on_fill(sell, 6, 61000)
    pos = oms.position(SS)
    expected = 10 * 1000 - fm.fee(SS, Side.BUY, 10, 60000) - fm.fee(SS, Side.SELL, 4, 61000) \
        - fm.fee(SS, Side.SELL, 6, 61000)
    assert pos.qty == 0
    assert pos.realized_pnl == pytest.approx(expected)
    assert sell.status.value == "FILLED"


# ---------- 리스크 ----------
def _risk(**kw):
    fm = FeeModel()
    oms = OrderManager(fm)
    limits = RiskLimits(kill_file="", **kw)
    return RiskManager(limits, oms, fm), oms


def test_risk_blocks_low_edge_entry():
    risk, _ = _risk()
    d = risk.check(Order(SS, Side.BUY, 5, 60000), expected_edge_rate=0.005)
    assert not d.ok and "왕복비용" in d.reason
    assert risk.check(Order(SS, Side.BUY, 5, 60000), expected_edge_rate=0.02).ok


def test_risk_blocks_short_and_oversize():
    risk, _ = _risk(max_order_krw=100_000)
    assert not risk.check(Order(SS, Side.SELL, 1, 60000)).ok
    assert "주문 금액" in risk.check(Order(SS, Side.BUY, 2, 60000), 0.05).reason


def test_daily_loss_kills_entries_but_allows_exit():
    risk, oms = _risk(max_daily_loss_krw=10_000)
    buy = oms.register(Order(SS, Side.BUY, 10, 60000))
    oms.on_fill(buy, 10, 60000)
    oms.update_mark(SS, 58000)  # -20,000원 평가손
    assert not risk.check(Order(ETF, Side.BUY, 1, 40000), 0.05).ok
    assert risk.killed
    assert risk.check(Order(SS, Side.SELL, 10, 58000)).ok
    risk.reset_day()
    assert not risk.killed


# ---------- 전략·백테스트 ----------
def _bars(prices, inst=SS, start=1_767_571_200):
    return [Bar(inst, start + i * 60, p, p, p, p, 1000) for i, p in enumerate(prices)]


def test_backtest_breakout_take_profit():
    prices = [60000] * 5 + [60500, 60600] + [62000] * 3
    strat = CostAwareBreakout(BreakoutParams(lookback=5, take_profit=0.015, stop_loss=0.01, order_value=600_000))
    bt = Backtester(strat, FeeModel(slippage_ticks=0))
    result = bt.run(_bars(prices))
    sides = [t["side"] for t in result.trades]
    assert sides == ["BUY", "SELL"]
    assert result.trades[0]["price"] == 60600  # 신호 다음 봉 시가 체결
    assert result.net_pnl_krw == pytest.approx(result.gross_pnl_krw - result.fees_krw)
    assert result.fees_krw > 0


def test_strategy_skips_when_target_below_cost():
    prices = [60000] * 5 + [60500] + [60500] * 3
    strat = CostAwareBreakout(BreakoutParams(lookback=5, take_profit=0.003, order_value=600_000))
    result = Backtester(strat).run(_bars(prices))
    assert result.trades == []


# ---------- 실시간 처리 ----------
def test_bar_aggregator():
    agg = BarAggregator(60)
    t = 1_000_020.0 - (1_000_020.0 % 60)
    assert agg.on_tick(SS, 100, 1, t + 1) == []
    agg.on_tick(SS, 105, 2, t + 10)
    agg.on_tick(SS, 99, 3, t + 20)
    bars = agg.flush(t + 61)
    assert len(bars) == 1
    b = bars[0]
    assert (b.open, b.high, b.low, b.close, b.volume) == (100, 105, 99, 99, 6)


def test_split_records_and_notice():
    fields = ["CUST", "ACNT", "0000012345", "", "02", "0", "00", "0", "005930", "3", "60000",
              "093001", "0", "2", "1"]
    payload = "^".join(fields + fields)
    recs = split_records("002", payload)
    assert len(recs) == 2
    n = parse_notice(recs[0], DOM_NOTICE_IDX)
    assert (n.order_no, n.side, n.qty, n.price, n.is_fill) == ("0000012345", Side.BUY, 3, 60000.0, True)


def test_aes_decrypt_roundtrip():
    key, iv = "k" * 32, "i" * 16
    padder = padding.PKCS7(128).padder()
    data = padder.update("hello^world".encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key.encode()), modes.CBC(iv.encode())).encryptor()
    b64 = base64.b64encode(enc.update(data) + enc.finalize()).decode()
    assert aes_decrypt(key, iv, b64) == "hello^world"


def test_oms_matches_broker_id_ignoring_zeros():
    oms = OrderManager(FeeModel())
    o = oms.register(Order(SS, Side.BUY, 1, 60000))
    oms.on_sent(o, "0000012345")
    assert oms.find_by_broker_id("12345") is o
    assert time.time() - o.created_ts < 5
