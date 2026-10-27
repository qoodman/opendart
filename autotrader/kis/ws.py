"""KIS 실시간 웹소켓: 체결가 시세 + 내 주문 체결통보.

- 끊기면 자동 재접속 후 다시 구독
- PINGPONG 메시지는 그대로 돌려보냄
- 체결통보는 AES-256-CBC 로 암호화되어 오므로 구독 응답의 key/iv 로 복호화

필드 순서(아래 *_IDX 상수)는 KIS 문서 기준이며, 모의투자에서 원본 로그(DEBUG)로
한 번 확인한 뒤 실전에 쓰세요.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..config import KisSettings
from ..models import US_EXCHANGES, Instrument, Market, Side
from .auth import TokenManager

log = logging.getLogger(__name__)

TR_DOMESTIC_TRADE = "H0STCNT0"   # 국내 실시간 체결가
TR_US_TRADE = "HDFSCNT0"         # 미국 실시간 체결가
TR_DOMESTIC_NOTICE = ("H0STCNI0", "H0STCNI9")  # (실전, 모의) 국내 체결통보
TR_US_NOTICE = ("H0GSCNI0", "H0GSCNI9")        # (실전, 모의) 해외 체결통보

# 국내 체결가: 종목코드, 체결시각, 현재가, ... 체결량
DOM_TRADE_IDX = {"symbol": 0, "time": 1, "price": 2, "volume": 12}
# 미국 체결가: RSYM, SYMB, ... LAST(11) ... EVOL(19)
US_TRADE_IDX = {"symbol": 1, "time": 5, "price": 11, "volume": 19}
# 체결통보 (국내/해외 필드 위치가 다름)
DOM_NOTICE_IDX = {"order_no": 2, "side": 4, "symbol": 8, "qty": 9, "price": 10, "filled": 13, "refused": 12}
US_NOTICE_IDX = {"order_no": 2, "side": 4, "symbol": 7, "qty": 8, "price": 9, "filled": 12, "refused": 11}


@dataclass
class Tick:
    instrument: Instrument
    price: float
    volume: float
    time: str


@dataclass
class FillNotice:
    order_no: str
    symbol: str
    side: Side
    qty: int
    price: float
    is_fill: bool       # False 면 접수·정정·취소 확인
    refused: bool


def aes_decrypt(key: str, iv: str, b64: str) -> str:
    cipher = Cipher(algorithms.AES(key.encode()), modes.CBC(iv.encode()))
    dec = cipher.decryptor()
    raw = dec.update(base64.b64decode(b64)) + dec.finalize()
    unpad = padding.PKCS7(128).unpadder()
    return (unpad.update(raw) + unpad.finalize()).decode("utf-8")


def split_records(count: str, payload: str) -> list[list[str]]:
    fields = payload.split("^")
    n = max(int(count or 1), 1)
    width = len(fields) // n
    return [fields[i * width:(i + 1) * width] for i in range(n)]


def parse_notice(fields: list[str], idx: dict[str, int]) -> FillNotice:
    return FillNotice(
        order_no=fields[idx["order_no"]],
        symbol=fields[idx["symbol"]].strip(),
        side=Side.SELL if fields[idx["side"]] == "01" else Side.BUY,
        qty=int(fields[idx["qty"]] or 0),
        price=float(fields[idx["price"]] or 0),
        is_fill=fields[idx["filled"]] == "2",
        refused=fields[idx["refused"]] == "1",
    )


TickHandler = Callable[[Tick], Awaitable[None]]
NoticeHandler = Callable[[FillNotice], Awaitable[None]]


class KisWebSocket:
    def __init__(self, settings: KisSettings, session: aiohttp.ClientSession, tokens: TokenManager,
                 instruments: list[Instrument], on_tick: TickHandler, on_notice: NoticeHandler):
        self.s = settings
        self.session = session
        self.tokens = tokens
        self.on_tick = on_tick
        self.on_notice = on_notice
        self._by_domestic = {i.symbol: i for i in instruments if i.market is not Market.US}
        self._by_us = {i.symbol: i for i in instruments if i.market is Market.US}
        self._aes: dict[str, tuple[str, str]] = {}
        self._stop = asyncio.Event()

    def _subscriptions(self) -> list[tuple[str, str]]:
        subs = [(TR_DOMESTIC_TRADE, s) for s in self._by_domestic]
        subs += [(TR_US_TRADE, US_EXCHANGES[i.exchange][2] + i.symbol) for i in self._by_us.values()]
        if self.s.hts_id:
            mode = 1 if self.s.paper else 0
            subs.append((TR_DOMESTIC_NOTICE[mode], self.s.hts_id))
            if self._by_us:
                subs.append((TR_US_NOTICE[mode], self.s.hts_id))
        else:
            log.warning("KIS_HTS_ID 가 없어 체결통보를 받지 못합니다. 체결 반영이 안 됩니다.")
        if len(subs) > 40:
            log.warning("구독 %d건: KIS 세션당 구독 한도(약 40건)를 넘을 수 있습니다", len(subs))
        return subs

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._run_once()
                backoff = 1.0
            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
                log.warning("웹소켓 끊김: %s. %.0f초 후 재접속", e, backoff)
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _run_once(self) -> None:
        approval = await self.tokens.approval_key()
        async with self.session.ws_connect(self.s.ws_url, heartbeat=None) as ws:
            for tr_id, key in self._subscriptions():
                await ws.send_str(json.dumps({
                    "header": {"approval_key": approval, "custtype": "P", "tr_type": "1",
                               "content-type": "utf-8"},
                    "body": {"input": {"tr_id": tr_id, "tr_key": key}},
                }))
            log.info("웹소켓 연결, 구독 완료")
            async for msg in ws:
                if self._stop.is_set():
                    break
                if msg.type is aiohttp.WSMsgType.TEXT:
                    await self._handle(ws, msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    raise ConnectionError(f"웹소켓 종료: {msg.type}")

    async def _handle(self, ws: aiohttp.ClientWebSocketResponse, text: str) -> None:
        if text[:1] in ("0", "1"):
            await self._handle_data(text)
            return
        data = json.loads(text)
        header = data.get("header", {})
        if header.get("tr_id") == "PINGPONG":
            await ws.send_str(text)
            return
        body = data.get("body", {})
        out = body.get("output") or {}
        if out.get("key") and out.get("iv"):
            self._aes[header.get("tr_id", "")] = (out["key"], out["iv"])
        if body.get("rt_cd") not in (None, "0"):
            log.error("구독 실패 %s: %s", header.get("tr_id"), body.get("msg1"))

    async def _handle_data(self, text: str) -> None:
        log.debug("RAW %s", text[:300])
        encrypted, tr_id, count, payload = text.split("|", 3)
        if encrypted == "1":
            key_iv = self._aes.get(tr_id)
            if not key_iv:
                log.error("%s 복호화 키 없음", tr_id)
                return
            payload = aes_decrypt(key_iv[0], key_iv[1], payload)

        for fields in split_records(count, payload):
            try:
                if tr_id == TR_DOMESTIC_TRADE:
                    inst = self._by_domestic.get(fields[DOM_TRADE_IDX["symbol"]])
                    if inst:
                        await self.on_tick(Tick(inst, float(fields[DOM_TRADE_IDX["price"]]),
                                                float(fields[DOM_TRADE_IDX["volume"]] or 0),
                                                fields[DOM_TRADE_IDX["time"]]))
                elif tr_id == TR_US_TRADE:
                    inst = self._by_us.get(fields[US_TRADE_IDX["symbol"]])
                    if inst:
                        await self.on_tick(Tick(inst, float(fields[US_TRADE_IDX["price"]]),
                                                float(fields[US_TRADE_IDX["volume"]] or 0),
                                                fields[US_TRADE_IDX["time"]]))
                elif tr_id in TR_DOMESTIC_NOTICE:
                    await self.on_notice(parse_notice(fields, DOM_NOTICE_IDX))
                elif tr_id in TR_US_NOTICE:
                    await self.on_notice(parse_notice(fields, US_NOTICE_IDX))
            except (IndexError, ValueError) as e:
                log.error("%s 메시지 해석 실패: %s / %s", tr_id, e, fields[:15])
