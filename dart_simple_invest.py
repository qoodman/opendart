# -*- coding: utf-8 -*-
"""
DART '상황보고' + 본문 '단순투자' 공시 → 날짜별 평균 매수단가 정리 (명령줄 버전)

창 화면이 필요하면 dart_app.py(또는 설치용 앱)를 쓰세요. 이 파일은 명령 프롬프트·터미널용입니다.
실제 로직은 dart_engine.py에 있어서 두 파일을 같은 폴더에 두어야 합니다.

필요: python -m pip install -r requirements.txt
"""
import sys, threading, multiprocessing

from dart_engine import Config, Engine, DartError, Stopped, HEARTBEAT

API_KEY   = "여기에_API_키"
START     = "20260101"        # 조회 시작일 (YYYYMMDD)
END       = "20261027"        # 조회 종료일
KEYWORD   = "상황보고"         # 보고서명 검색어
BODY_WORD = "단순투자"         # 본문 포함 단어
OUT       = "단순투자_평균매수단가.xlsx"

WORKERS      = 8              # 동시 다운로드 수
MAX_PER_MIN  = 900            # 분당 최대 호출 (OpenDART 과다호출 차단 방지용)
DAILY_BUDGET = 39000          # 이번 실행에서 쓸 최대 호출 수 (한도 4만 건 여유분)


def main():
    cfg = Config(api_key=API_KEY, start=START, end=END, keyword=KEYWORD, body_word=BODY_WORD,
                 out_dir=".", out_name=OUT, workers=WORKERS, max_per_min=MAX_PER_MIN,
                 daily_budget=DAILY_BUDGET)
    eng = Engine(cfg)

    # 생존 신호: 진행 표시가 한동안 안 나와도 ♥ 줄이 HEARTBEAT초마다 찍히면 살아 있는 것
    def heartbeat():
        while not eng.stop.wait(HEARTBEAT):
            print(eng.heartbeat_line(), flush=True)
    threading.Thread(target=heartbeat, daemon=True).start()
    print(f"DART 연결 확인 중... (♥ 표시가 {HEARTBEAT}초마다 나오면 정상 작동 중)", flush=True)

    try:
        eng.run()
    except DartError as e:
        raise SystemExit(str(e))
    except (KeyboardInterrupt, Stopped):
        eng.stop.set()
        raise SystemExit("\n중지했습니다. 받은 건 저장돼 있어서 다시 실행하면 이어서 받습니다.")
    finally:
        eng.stop.set()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
