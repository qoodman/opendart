"""KIS REST 클라이언트를 가짜 서버로 검증 (실제 증권사 호출 없음)."""
import pytest
from aiohttp import ClientSession, web

from autotrader.config import KisSettings
from autotrader.kis.auth import TokenManager
from autotrader.kis.rest import KisApiError, KisRestClient
from autotrader.models import Side


@pytest.fixture
def fake_kis():
    calls = []
    state = {"rate_limited_once": False}

    async def token(request):
        return web.json_response({"access_token": "TKN", "access_token_token_expired": "2099-01-01 00:00:00"})

    async def order(request):
        body = await request.json()
        calls.append((request.headers["tr_id"], body))
        if not state["rate_limited_once"]:
            state["rate_limited_once"] = True
            return web.json_response({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"})
        if body["PDNO"] == "BAD":
            return web.json_response({"rt_cd": "1", "msg_cd": "APBK0013", "msg1": "주문 불가"})
        return web.json_response({"rt_cd": "0", "output": {"ODNO": "0000099", "KRX_FWDG_ORD_ORGNO": "06010"}})

    app = web.Application()
    app.router.add_post("/oauth2/tokenP", token)
    app.router.add_post("/uapi/domestic-stock/v1/trading/order-cash", order)
    return app, calls


async def test_domestic_order_retry_and_headers(fake_kis, tmp_path, monkeypatch):
    app, calls = fake_kis
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr("autotrader.config.PAPER_REST", f"http://127.0.0.1:{port}")

    settings = KisSettings("APPKEY123456", "SECRET", "12345678", "01", paper=True, max_requests_per_sec=50)
    try:
        async with ClientSession() as session:
            client = KisRestClient(settings, session, TokenManager(settings, session, cache_dir=tmp_path))
            ack = await client.domestic_order("005930", Side.BUY, 3, None)
            assert (ack.order_no, ack.org_no) == ("0000099", "06010")
            assert len(calls) == 2  # 호출 한도 초과 1회 후 재시도
            tr_id, body = calls[-1]
            assert tr_id == "VTTC0012U"
            assert body["ORD_DVSN"] == "01" and body["ORD_QTY"] == "3" and body["CANO"] == "12345678"

            with pytest.raises(KisApiError) as e:
                await client.domestic_order("BAD", Side.SELL, 1, 60000)
            assert e.value.msg_cd == "APBK0013"
            assert calls[-1][0] == "VTTC0011U"
            assert list(tmp_path.glob("kis_token_paper_*.json"))
    finally:
        await runner.cleanup()
