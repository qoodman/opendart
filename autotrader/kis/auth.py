"""KIS 접근 토큰·웹소켓 접속키 관리.

토큰은 약 24시간 유효하고 재발급 횟수가 제한되므로 파일에 저장해 재사용합니다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import aiohttp

from ..config import KisSettings

log = logging.getLogger(__name__)

REFRESH_MARGIN_SEC = 60 * 60  # 만료 1시간 전부터 새로 발급


class TokenManager:
    def __init__(self, settings: KisSettings, session: aiohttp.ClientSession,
                 cache_dir: str | Path | None = None):
        self.s = settings
        self.session = session
        base = Path(cache_dir or os.environ.get("KIS_TOKEN_DIR", Path.home() / ".cache" / "autotrader"))
        mode = "paper" if settings.paper else "real"
        self.cache_path = base / f"kis_token_{mode}_{settings.app_key[-6:]}.json"
        self._token: str = ""
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    def _load_cache(self) -> None:
        try:
            data = json.loads(self.cache_path.read_text())
            self._token, self._expires_at = data["access_token"], float(data["expires_at"])
        except (OSError, ValueError, KeyError):
            pass

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps({"access_token": self._token, "expires_at": self._expires_at}))
        os.chmod(self.cache_path, 0o600)

    async def token(self) -> str:
        async with self._lock:
            if not self._token:
                self._load_cache()
            if self._token and time.time() < self._expires_at - REFRESH_MARGIN_SEC:
                return self._token
            await self._issue()
            return self._token

    async def _issue(self) -> None:
        body = {"grant_type": "client_credentials", "appkey": self.s.app_key, "appsecret": self.s.app_secret}
        async with self.session.post(f"{self.s.rest_url}/oauth2/tokenP", json=body) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or "access_token" not in data:
                raise RuntimeError(f"토큰 발급 실패 ({resp.status}): {data}")
        self._token = data["access_token"]
        expired = data.get("access_token_token_expired")
        if expired:
            self._expires_at = datetime.strptime(expired, "%Y-%m-%d %H:%M:%S").timestamp()
        else:
            self._expires_at = time.time() + int(data.get("expires_in", 86400))
        self._save_cache()
        log.info("KIS 접근 토큰 발급 (만료 %s)", expired)

    async def approval_key(self) -> str:
        """웹소켓 접속키."""
        body = {"grant_type": "client_credentials", "appkey": self.s.app_key, "secretkey": self.s.app_secret}
        async with self.session.post(f"{self.s.rest_url}/oauth2/Approval", json=body) as resp:
            data = await resp.json(content_type=None)
            if "approval_key" not in data:
                raise RuntimeError(f"웹소켓 접속키 발급 실패: {data}")
            return data["approval_key"]
