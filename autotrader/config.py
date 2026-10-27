"""환경변수 설정. .env.example 참고."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REAL_REST = "https://openapi.koreainvestment.com:9443"
PAPER_REST = "https://openapivts.koreainvestment.com:29443"
REAL_WS = "ws://ops.koreainvestment.com:21000"
PAPER_WS = "ws://ops.koreainvestment.com:31000"


def load_dotenv(path: str | Path = ".env") -> None:
    """간단한 .env 로더 (이미 설정된 환경변수는 덮어쓰지 않음)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class KisSettings:
    app_key: str
    app_secret: str
    account_no: str          # 계좌번호 앞 8자리
    account_product: str     # 계좌상품코드 뒤 2자리 (보통 01)
    paper: bool = True       # True: 모의투자
    hts_id: str = ""         # 체결통보 구독용 HTS ID
    max_requests_per_sec: float = 0.0  # 0이면 실전 15, 모의 2

    @property
    def rest_url(self) -> str:
        return PAPER_REST if self.paper else REAL_REST

    @property
    def ws_url(self) -> str:
        return PAPER_WS if self.paper else REAL_WS

    @property
    def rate(self) -> float:
        return self.max_requests_per_sec or (2.0 if self.paper else 15.0)

    @classmethod
    def from_env(cls, live: bool = False) -> "KisSettings":
        load_dotenv()
        if live and os.environ.get("KIS_ALLOW_LIVE", "").lower() != "yes":
            raise RuntimeError("실전 매매는 환경변수 KIS_ALLOW_LIVE=yes 를 함께 설정해야 합니다")
        prefix = "KIS_" if live else "KIS_PAPER_"
        account = os.environ.get(f"{prefix}ACCOUNT", "")
        if "-" in account:
            no, prod = account.split("-", 1)
        else:
            no, prod = account[:8], account[8:] or "01"
        try:
            return cls(
                app_key=os.environ[f"{prefix}APP_KEY"],
                app_secret=os.environ[f"{prefix}APP_SECRET"],
                account_no=no,
                account_product=prod,
                paper=not live,
                hts_id=os.environ.get("KIS_HTS_ID", ""),
                max_requests_per_sec=float(os.environ.get("KIS_MAX_RPS", "0") or 0),
            )
        except KeyError as e:
            raise RuntimeError(f"환경변수 {e.args[0]} 가 없습니다. .env.example 을 참고하세요") from None
