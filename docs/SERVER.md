# 서버로 돌리기 (오라클 클라우드, 부동산 앱과 같은 서버)

맥을 켜두지 않아도 매일 저녁 새 공시를 받아 분석하고, 폰에서 결과를 보고 알림을 받기 위한 안내입니다.
부동산 앱(`realestate`)을 이미 서버에 올려 두었다는 전제입니다. 서버·Tailscale·ntfy를 그대로 같이 씁니다.

```
[오라클 서버]  realestate.xxxx.ts.net (부동산)   +   dart.xxxx.ts.net (DART, 매일 20:13 수집)
     │  Tailscale (나만 접속 가능한 사설망)
[안드로이드 폰]  홈 화면 아이콘 2개  +  ntfy 알림 (같은 주제로 같이 받음)
```

## 1. 서버에 올리기 (맥에서, 10분)

맥 터미널에서 실행합니다. 서버 주소와 키는 부동산 앱 때와 같습니다.

```bash
git clone https://github.com/qoodman/opendart.git ~/opendart   # 처음 한 번
cd ~/opendart
./scripts/push_to_server.sh ubuntu@서버IP ~/.ssh/oracle.key --with-data
```

이 명령이 하는 일:
- 서버에 코드를 받고 설치합니다 (`~/opendart`).
- **--with-data**: 맥 앱이 받아 둔 원문(`문서/DART단순투자/dart_cache`)을 서버로 복사합니다. 서버가 처음부터 수만 건을 다시 받지 않아도 됩니다.
- 맥 앱에 넣어 둔 **API 키**를 서버 설정(`.env`)에 넣습니다.
- 부동산 앱의 **ntfy 주제**와 **Tailscale 주소**를 찾아 자동으로 설정합니다.
- 매일 20:13 자동 수집과 대시보드를 등록합니다.

중간에 **DART 전용 Tailscale 로그인 주소**가 한 번 나옵니다. 브라우저에서 열고 **폰과 같은 계정**으로 로그인하세요.
마지막에 `폰 대시보드: https://dart.xxxx.ts.net` 같은 **DART 전용 주소**가 나옵니다. 부동산 주소(`realestate.xxxx.ts.net`)와 따로입니다.

> 같은 서버지만 Tailscale에는 `dart`라는 기기가 하나 더 생깁니다. Tailscale 관리 화면(login.tailscale.com → Machines)에서
> `dart` 옆 **⋯ → Disable key expiry**를 눌러 두면 180일마다 다시 로그인할 필요가 없습니다(부동산 서버도 마찬가지).

## 2. 접속 확인 (일본 서버라면 꼭)

```bash
ssh -i ~/.ssh/oracle.key ubuntu@서버IP "~/opendart/.venv/bin/python ~/opendart/dart_server.py net-check"
```
- 모두 ✔이면 그대로 진행합니다.
- **DART OpenAPI에 ✖**가 나오면 해외 접속이 막힌 것입니다. 부동산 앱 안내의 **"한국 서버가 필요할 때"**(AWS Lightsail 서울)를 보세요.
- **네이버 증권에 ✖**만 나오면 종가 칸만 비고 나머지는 정상으로 돌아갑니다.

## 3. 첫 수집

```bash
ssh -i ~/.ssh/oracle.key ubuntu@서버IP
sudo systemctl start --no-block dart-daily
journalctl -u dart-daily -f        # 진행 상황 보기 (Ctrl+C로 보기만 끔, 수집은 계속됨)
```
- 30초마다 `♥` 생존 신호 줄이 찍힙니다. 폰 대시보드에서도 진행 막대로 보입니다.
- **첫 수집은 알림을 보내지 않고 기준선만 저장**합니다. 다음 날부터 새로 들어온 공시를 알려줍니다.
- 원문을 복사하지 않았다면 처음엔 오래 걸리고, 하루 호출 한도를 넘으면 다음 날 이어서 받습니다.

## 4. 폰에서 열기

1. 폰에서 Tailscale을 켠 상태로 1번에서 나온 주소(`https://dart.xxxx.ts.net`)를 크롬에서 엽니다.
2. 크롬 메뉴(⋮) → **홈 화면에 추가**. 부동산 아이콘 옆에 파란 막대그래프 아이콘이 생깁니다.
3. 대시보드 위쪽 **부동산 ↗** 링크로 부동산 대시보드로 바로 갈 수 있습니다.

| 화면 | 내용 |
|---|---|
| 요약 | 날짜별 평균 매수단가. 종목명·코드·제출인 검색, 정렬(제출일·변동일·매수금액·종가가 매수단가보다 낮은 순) |
| 새 공시 | 최근 2주 동안 새로 들어온 공시 (알림을 누르면 여기로 옴) |
| 종목 화면 | 그 종목의 모든 매수·매도 내역, DART 원문·네이버 증권 링크 |
| 엑셀 | 마지막 수집 결과 엑셀 내려받기 |
| 지금 수집 | 기다리지 않고 바로 수집 시작 |

## 5. 알림

부동산 앱과 같은 ntfy 주제를 쓰므로 **폰에서 따로 설정할 게 없습니다.** 새 공시가 있는 날 저녁에 이렇게 옵니다.

```
새 단순투자 공시 3건
삼성전자 — ○○자산운용 매수 12,000주 평균 71,300원 (제출일 종가 70,800원)
…
```
시험: `~/opendart/.venv/bin/python ~/opendart/dart_server.py notify-test`
따로 받고 싶으면 `.env`의 `NTFY_TOPIC`을 다른 이름으로 바꾸고 ntfy 앱에서 그 주제를 구독하세요.

## 평소 사용

| 하고 싶은 일 | 방법 |
|---|---|
| 결과 보기 | 폰 홈 화면의 파란 아이콘 (Tailscale 켜진 상태) |
| 코드 업데이트 반영 | 맥에서 `cd ~/opendart && git pull && ./scripts/push_to_server.sh` |
| 지금 바로 수집 | 대시보드의 **지금 수집**, 또는 서버에서 `sudo systemctl start --no-block dart-daily` |
| 수집 기록 보기 | 서버에서 `journalctl -u dart-daily -n 50` |
| 설정 바꾸기 | 서버에서 `nano ~/opendart/.env` (기간 `DART_DAYS`, 검색어 등) |

## 문제가 생기면

- **대시보드가 안 열림**: 폰의 Tailscale이 켜져 있는지 확인합니다. 서버에서 `systemctl status dart-web tailscaled-dart`로 둘 다 active인지 봅니다.
- **수집 실패 알림이 옴**: 서버에서 `journalctl -u dart-daily -n 50`으로 이유를 봅니다. "호출 한도"면 다음 날 자동으로 이어서 받습니다.
- **부동산 앱이 느려짐**: DART 수집은 낮은 우선순위로 돌고 부동산 수집(19:17)과 시간이 겹치지 않게 20:13에 돕니다. 그래도 느리면 `.env`의 `DART_WORKERS`를 2로 줄이세요.
