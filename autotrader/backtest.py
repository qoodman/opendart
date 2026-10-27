"""봉 데이터 백테스터.

- 신호는 봉 종가에서 만들고, 다음 봉 시가에 체결된다고 가정 (미래 정보 사용 방지)
- 체결가에 슬리피지(호가 n틱) 반영, 수수료·세금은 FeeModel 로 차감
- 실전과 같은 RiskManager 를 통과한 주문만 체결

CSV 형식: timestamp,symbol,open,high,low,close,volume
  timestamp 는 epoch 초 또는 ISO 형식(2026-01-02T09:00:00)
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .fees import FeeModel, tick_size
from .models import Bar, Instrument, Order, Side
from .oms import OrderManager
from .risk import RiskLimits, RiskManager
from .strategy.base import Strategy, StrategyContext


def _parse_ts(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return datetime.fromisoformat(value).timestamp()


def load_bars_csv(path: str | Path, instruments: dict[str, Instrument]) -> list[Bar]:
    bars: list[Bar] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            inst = instruments.get(row["symbol"])
            if inst is None:
                continue
            bars.append(Bar(inst, _parse_ts(row["timestamp"]), float(row["open"]), float(row["high"]),
                            float(row["low"]), float(row["close"]), float(row.get("volume") or 0)))
    bars.sort(key=lambda b: b.ts)
    return bars


@dataclass
class BacktestResult:
    trades: list[dict]
    equity_curve: list[float]
    gross_pnl_krw: float
    fees_krw: float
    net_pnl_krw: float
    round_trips: int
    win_rate: float
    max_drawdown_krw: float
    rejected: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"왕복 거래 수     : {self.round_trips}",
            f"승률             : {self.win_rate:.1%}",
            f"총손익(비용 전)  : {self.gross_pnl_krw:,.0f}원",
            f"수수료·세금      : {self.fees_krw:,.0f}원",
            f"순손익           : {self.net_pnl_krw:,.0f}원",
            f"최대 낙폭        : {self.max_drawdown_krw:,.0f}원",
        ]
        if self.gross_pnl_krw > 0:
            lines.append(f"비용/총손익 비율 : {self.fees_krw / self.gross_pnl_krw:.1%}")
        if self.rejected:
            lines.append("리스크 거부      : " + ", ".join(f"{k} {v}건" for k, v in self.rejected.items()))
        return "\n".join(lines)


class Backtester:
    def __init__(self, strategy: Strategy, fee_model: FeeModel | None = None,
                 limits: RiskLimits | None = None, usdkrw: float = 1400.0):
        self.strategy = strategy
        self.fee_model = fee_model or FeeModel()
        self.oms = OrderManager(self.fee_model, usdkrw=usdkrw)
        limits = limits or RiskLimits()
        limits.kill_file = ""  # 백테스트에서는 파일 킬 스위치 무시
        self.risk = RiskManager(limits, self.oms, self.fee_model)

    def _fill_price(self, order: Order, open_price: float) -> float:
        slip = self.fee_model.slippage_ticks * tick_size(order.instrument.market, open_price)
        return open_price + slip if order.side is Side.BUY else max(open_price - slip, 0.0)

    def run(self, bars: list[Bar]) -> BacktestResult:
        pending: dict[str, list[Order]] = {}
        equity: list[float] = []
        rejected: dict[str, int] = {}
        day = None

        for bar in bars:
            bar_day = datetime.fromtimestamp(bar.ts).date()
            if bar_day != day:
                day = bar_day
                self.risk.reset_day()
            key = bar.instrument.key
            # 1) 이전 봉에서 낸 주문을 이번 봉 시가에 체결
            for order in pending.pop(key, []):
                self.oms.on_fill(order, order.qty, self._fill_price(order, bar.open), ts=bar.ts)

            # 2) 평가 갱신, 손실 한도 확인
            self.oms.update_mark(bar.instrument, bar.close)
            self.risk.check_daily_loss()

            # 3) 전략 신호 → 리스크 검사 → 다음 봉 체결 대기
            pos = self.oms.position(bar.instrument)
            ctx = StrategyContext(self.fee_model, pos,
                                  self.oms.pending_qty(bar.instrument, Side.BUY),
                                  self.oms.pending_qty(bar.instrument, Side.SELL),
                                  self.oms.to_krw(1.0, bar.instrument.currency))
            for sig in self.strategy.on_bar(bar, ctx):
                order = Order(sig.instrument, sig.side, sig.qty, sig.price, sig.reason)
                decision = self.risk.check(order, sig.expected_edge_rate, now=bar.ts)
                self.oms.register(order)
                if not decision.ok:
                    self.oms.on_reject(order, decision.reason)
                    label = decision.reason.split(" (")[0]
                    rejected[label] = rejected.get(label, 0) + 1
                    continue
                self.oms.on_sent(order, broker_order_id=f"BT{order.client_id}")
                pending.setdefault(key, []).append(order)

            equity.append(self.oms.realized_pnl_krw() + self.oms.unrealized_pnl_krw())

        return self._result(equity, rejected)

    def _result(self, equity: list[float], rejected: dict[str, int]) -> BacktestResult:
        trades = self.oms.trade_log
        fees = self.oms.total_fees_krw()
        net = equity[-1] if equity else 0.0
        sells = [t for t in trades if t["side"] == "SELL"]
        wins = 0
        for t in sells:
            fx = self.oms.usdkrw if t["market"] == "US" else 1.0
            # 매도 1건의 손익에서 그 매도의 비용과, 대응되는 매수 비용을 근사로 차감
            if (t["realized"] - 2 * t["fee"]) * fx > 0:
                wins += 1
        peak, mdd = -math.inf, 0.0
        for v in equity:
            peak = max(peak, v)
            mdd = max(mdd, peak - v)
        return BacktestResult(
            trades=trades, equity_curve=equity, gross_pnl_krw=net + fees, fees_krw=fees,
            net_pnl_krw=net, round_trips=len(sells),
            win_rate=wins / len(sells) if sells else 0.0, max_drawdown_krw=mdd, rejected=rejected,
        )
