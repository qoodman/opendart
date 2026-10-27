"""명령줄 도구.

  python -m autotrader costs                         # 시장별 왕복 비용 표
  python -m autotrader sample-data sample.csv        # 연습용 가짜 데이터 생성
  python -m autotrader backtest sample.csv -s 005930:KRX_STOCK -s AAPL:US:NASD
  python -m autotrader run -s 005930:KRX_STOCK       # 모의투자 실시간 매매
  python -m autotrader run -s 005930:KRX_STOCK --live  # 실전 (KIS_ALLOW_LIVE=yes 필요)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import math
import random
from datetime import datetime, timedelta

from .backtest import Backtester, load_bars_csv
from .fees import FeeModel, round_to_tick
from .models import Instrument, Market, Side
from .risk import RiskLimits
from .strategy import BreakoutParams, CostAwareBreakout


def parse_instrument(spec: str) -> Instrument:
    """'005930:KRX_STOCK', '069500:KRX_ETF', 'AAPL:US:NASD'."""
    parts = spec.split(":")
    if len(parts) < 2:
        raise argparse.ArgumentTypeError("형식: 종목:시장[:거래소] 예) 005930:KRX_STOCK, AAPL:US:NASD")
    try:
        market = Market(parts[1].upper())
    except ValueError:
        raise argparse.ArgumentTypeError(f"시장은 {[m.value for m in Market]} 중 하나") from None
    return Instrument(parts[0].upper() if market is Market.US else parts[0], market,
                      parts[2].upper() if len(parts) > 2 else "")


def cmd_costs(args: argparse.Namespace) -> None:
    fm = FeeModel(slippage_ticks=args.slippage)
    examples = [
        (Instrument("005930", Market.KRX_STOCK), 60000, "국내주식 6만원대"),
        (Instrument("000000", Market.KRX_STOCK), 3000, "국내주식 3천원대"),
        (Instrument("069500", Market.KRX_ETF), 40000, "국내 ETF 4만원대"),
        (Instrument("AAPL", Market.US, "NASD"), 230.0, "미국주식 $230"),
        (Instrument("SPY", Market.US, "NYSE"), 600.0, "미국 ETF $600"),
    ]
    print(f"슬리피지 가정: 매수·매도 각 {args.slippage}틱\n")
    print(f"{'구분':<18}{'수수료+세금':>12}{'슬리피지 포함':>14}{'목표수익 최소(x2)':>18}")
    for inst, price, label in examples:
        qty = max(1, int(args.amount // price)) if inst.market is not Market.US else max(1, int(args.amount / 1400 // price))
        no_slip = fm.round_trip_cost_rate(inst, price, qty, include_slippage=False)
        full = fm.round_trip_cost_rate(inst, price, qty)
        print(f"{label:<18}{no_slip:>12.3%}{full:>14.3%}{full * 2:>18.3%}")
    print("\n'목표수익 최소' 보다 작은 수익을 노리는 진입은 리스크 관리에서 막힙니다.")


def cmd_sample_data(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    specs = [("005930", 60000.0, Market.KRX_STOCK), ("069500", 40000.0, Market.KRX_ETF), ("AAPL", 230.0, Market.US)]
    start = datetime(2026, 1, 5, 9, 0)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "symbol", "open", "high", "low", "close", "volume"])
        for day in range(args.days):
            t0 = start + timedelta(days=day)
            prices = {s: p for s, p, _ in specs}
            for minute in range(390):
                ts = t0 + timedelta(minutes=minute)
                for symbol, _, market in specs:
                    p = prices[symbol]
                    drift = 0.00002 * math.sin(minute / 40 + day)
                    o = p
                    c = max(p * (1 + drift + rng.gauss(0, 0.0012)), 1)
                    h = max(o, c) * (1 + abs(rng.gauss(0, 0.0005)))
                    l = min(o, c) * (1 - abs(rng.gauss(0, 0.0005)))
                    vals = [round_to_tick(market, v, Side.BUY) for v in (o, h, l, c)]
                    prices[symbol] = c
                    w.writerow([ts.isoformat(), symbol, *vals, rng.randint(100, 5000)])
    print(f"{args.out} 생성 ({args.days}일, 1분봉, 005930/069500/AAPL)")


def _strategy(args: argparse.Namespace) -> CostAwareBreakout:
    return CostAwareBreakout(BreakoutParams(
        lookback=args.lookback, take_profit=args.take_profit, stop_loss=args.stop_loss,
        order_value=args.order_value, min_edge_multiple=args.min_edge,
    ))


def _limits(args: argparse.Namespace) -> RiskLimits:
    return RiskLimits(max_daily_loss_krw=args.max_daily_loss, min_edge_multiple=args.min_edge)


def cmd_backtest(args: argparse.Namespace) -> None:
    instruments = {i.symbol: i for i in args.symbol}
    bars = load_bars_csv(args.csv, instruments)
    if not bars:
        raise SystemExit("데이터가 없습니다. CSV 의 symbol 과 -s 옵션을 확인하세요")
    bt = Backtester(_strategy(args), FeeModel(slippage_ticks=args.slippage), _limits(args), usdkrw=args.usdkrw)
    result = bt.run(bars)
    print(result.summary())


def cmd_run(args: argparse.Namespace) -> None:
    from .config import KisSettings
    from .engine import LiveEngine

    settings = KisSettings.from_env(live=args.live)
    engine = LiveEngine(settings, list(args.symbol), _strategy(args), FeeModel(slippage_ticks=args.slippage),
                        _limits(args), usdkrw=args.usdkrw)
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        pass


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="autotrader", description="KIS 중빈도 자동매매")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("costs", help="시장별 왕복 거래비용")
    p.add_argument("--amount", type=float, default=1_000_000, help="거래 금액(원)")
    p.add_argument("--slippage", type=float, default=1.0, help="슬리피지(틱)")
    p.set_defaults(func=cmd_costs)

    p = sub.add_parser("sample-data", help="연습용 1분봉 CSV 생성")
    p.add_argument("out")
    p.add_argument("--days", type=int, default=20)
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=cmd_sample_data)

    for name, func, help_ in (("backtest", cmd_backtest, "CSV 로 백테스트"),
                              ("run", cmd_run, "실시간 매매 (기본 모의투자)")):
        p = sub.add_parser(name, help=help_)
        if name == "backtest":
            p.add_argument("csv")
        else:
            p.add_argument("--live", action="store_true", help="실전 계좌 (KIS_ALLOW_LIVE=yes 필요)")
        p.add_argument("-s", "--symbol", type=parse_instrument, action="append", required=True)
        p.add_argument("--lookback", type=int, default=20)
        p.add_argument("--take-profit", type=float, default=0.015)
        p.add_argument("--stop-loss", type=float, default=0.008)
        p.add_argument("--order-value", type=float, default=500_000, help="1회 진입 금액(원, 미국은 환율로 환산)")
        p.add_argument("--min-edge", type=float, default=2.0, help="목표수익 / 왕복비용 최소 배수")
        p.add_argument("--max-daily-loss", type=float, default=300_000)
        p.add_argument("--slippage", type=float, default=1.0)
        p.add_argument("--usdkrw", type=float, default=1400.0)
        p.set_defaults(func=func)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "backtest" and not args.verbose:
        logging.getLogger("autotrader").setLevel(logging.WARNING)
    args.func(args)
