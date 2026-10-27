"""KIS REST API 클라이언트 (국내 주식·ETF, 미국 주식).

거래ID(tr_id)는 KIS 가 바꾸는 경우가 있으니 KIS Developers 문서와 다르면 아래 표를 고치세요.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from ..config import KisSettings
from ..models import US_EXCHANGES, Instrument, Market, Side
from .auth import TokenManager
from .ratelimit import RateLimiter

log = logging.getLogger(__name__)

# (실전, 모의) 거래ID
TR = {
    "domestic_buy": ("TTTC0012U", "VTTC0012U"),
    "domestic_sell": ("TTTC0011U", "VTTC0011U"),
    "domestic_cancel": ("TTTC0013U", "VTTC0013U"),
    "domestic_balance": ("TTTC8434R", "VTTC8434R"),
    "domestic_price": ("FHKST01010100", "FHKST01010100"),
    "us_buy": ("TTTT1002U", "VTTT1002U"),
    "us_sell": ("TTTT1006U", "VTTT1001U"),
    "us_cancel": ("TTTT1004U", "VTTT1004U"),
    "us_price": ("HHDFS00000300", "HHDFS00000300"),
}

RATE_LIMIT_CODE = "EGW00201"  # 초당 거래건수 초과


class KisApiError(RuntimeError):
    def __init__(self, msg_cd: str, msg: str, payload: dict | None = None):
        super().__init__(f"[{msg_cd}] {msg}")
        self.msg_cd = msg_cd
        self.payload = payload or {}


@dataclass
class OrderAck:
    order_no: str
    org_no: str = ""


class KisRestClient:
    def __init__(self, settings: KisSettings, session: aiohttp.ClientSession,
                 tokens: TokenManager | None = None):
        self.s = settings
        self.session = session
        self.tokens = tokens or TokenManager(settings, session)
        self.limiter = RateLimiter(settings.rate)

    def tr(self, name: str) -> str:
        real, paper = TR[name]
        return paper if self.s.paper else real

    @property
    def _account(self) -> dict[str, str]:
        return {"CANO": self.s.account_no, "ACNT_PRDT_CD": self.s.account_product}

    async def _request(self, method: str, path: str, tr_id: str, *,
                       params: dict | None = None, body: dict | None = None) -> dict[str, Any]:
        for attempt in range(4):
            await self.limiter.wait()
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {await self.tokens.token()}",
                "appkey": self.s.app_key,
                "appsecret": self.s.app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            }
            async with self.session.request(method, f"{self.s.rest_url}{path}", headers=headers,
                                            params=params, json=body) as resp:
                data = await resp.json(content_type=None)
            if data.get("rt_cd") == "0":
                return data
            msg_cd = data.get("msg_cd", str(resp.status))
            # 게이트웨이에서 거절된 호출 초과는 접수되지 않았으므로 재시도해도 안전
            if msg_cd == RATE_LIMIT_CODE and attempt < 3:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise KisApiError(msg_cd, data.get("msg1", "알 수 없는 오류"), data)
        raise KisApiError(RATE_LIMIT_CODE, "호출 한도 초과 재시도 실패")

    # ---------------- 국내 (주식·ETF) ----------------
    async def domestic_price(self, symbol: str) -> dict:
        data = await self._request("GET", "/uapi/domestic-stock/v1/quotations/inquire-price",
                                   self.tr("domestic_price"),
                                   params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol})
        return data["output"]

    async def domestic_order(self, symbol: str, side: Side, qty: int, price: int | None) -> OrderAck:
        """price=None 이면 시장가."""
        body = {
            **self._account,
            "PDNO": symbol,
            "ORD_DVSN": "01" if price is None else "00",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0" if price is None else str(int(price)),
        }
        tr = self.tr("domestic_buy" if side is Side.BUY else "domestic_sell")
        out = (await self._request("POST", "/uapi/domestic-stock/v1/trading/order-cash", tr, body=body))["output"]
        return OrderAck(out["ODNO"], out.get("KRX_FWDG_ORD_ORGNO", ""))

    async def domestic_cancel(self, order_no: str, org_no: str) -> None:
        body = {
            **self._account,
            "KRX_FWDG_ORD_ORGNO": org_no,
            "ORGN_ODNO": order_no,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # 02 = 취소
            "ORD_QTY": "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",      # 잔량 전부
        }
        await self._request("POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl",
                            self.tr("domestic_cancel"), body=body)

    async def domestic_balance(self) -> list[dict]:
        """보유 종목 목록 (첫 페이지)."""
        params = {
            **self._account,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        data = await self._request("GET", "/uapi/domestic-stock/v1/trading/inquire-balance",
                                   self.tr("domestic_balance"), params=params)
        return [r for r in data.get("output1", []) if int(r.get("hldg_qty", "0") or 0) > 0]

    # ---------------- 미국 ----------------
    async def us_price(self, symbol: str, exchange: str) -> dict:
        excd = US_EXCHANGES[exchange][1]
        data = await self._request("GET", "/uapi/overseas-price/v1/quotations/price", self.tr("us_price"),
                                   params={"AUTH": "", "EXCD": excd, "SYMB": symbol})
        return data["output"]

    async def us_order(self, symbol: str, exchange: str, side: Side, qty: int, price: float) -> OrderAck:
        """미국 주식은 지정가만 사용 (시장가 대신 체결 잘 되는 지정가를 넣으세요)."""
        body = {
            **self._account,
            "OVRS_EXCG_CD": US_EXCHANGES[exchange][0],
            "PDNO": symbol,
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": f"{price:.2f}" if price >= 1 else f"{price:.4f}",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",
        }
        tr = self.tr("us_buy" if side is Side.BUY else "us_sell")
        out = (await self._request("POST", "/uapi/overseas-stock/v1/trading/order", tr, body=body))["output"]
        return OrderAck(out["ODNO"], out.get("KRX_FWDG_ORD_ORGNO", ""))

    async def us_cancel(self, symbol: str, exchange: str, order_no: str, qty: int) -> None:
        body = {
            **self._account,
            "OVRS_EXCG_CD": US_EXCHANGES[exchange][0],
            "PDNO": symbol,
            "ORGN_ODNO": order_no,
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": "0",
            "ORD_SVR_DVSN_CD": "0",
        }
        await self._request("POST", "/uapi/overseas-stock/v1/trading/order-rvsecncl",
                            self.tr("us_cancel"), body=body)

    # ---------------- 공통 ----------------
    async def last_price(self, inst: Instrument) -> float:
        if inst.market is Market.US:
            return float((await self.us_price(inst.symbol, inst.exchange))["last"])
        return float((await self.domestic_price(inst.symbol))["stck_prpr"])
