# DART 단순투자 평균매수단가

DART의 '상황보고' 공시 중 본문에 '단순투자'가 들어간 것을 모아 **날짜별 평균 매수단가**를 엑셀로 정리합니다.
Windows와 Mac에서 쓸 수 있는 창 화면 앱과, 기존 명령줄 버전이 있습니다.

## 앱 받기

1. GitHub 저장소의 **Actions** 탭 → 가장 위의 초록색 ✔ **앱 빌드** 실행을 누릅니다.
2. 아래 **Artifacts**에서 내 컴퓨터에 맞는 것을 받습니다.
   - `DART-simple-invest-windows` : Windows
   - `DART-simple-invest-mac-apple-silicon` : M1·M2·M3·M4 맥 ( 메뉴 → 이 Mac에 관하여에서 '칩: Apple …')
   - `DART-simple-invest-mac-intel` : 인텔 맥 ('프로세서: Intel …')
3. 받은 zip을 풀면 앱이 나옵니다. (`v1.0.0` 같은 태그를 올리면 **Releases**에도 자동으로 올라갑니다.)

### Windows에서 처음 열 때
"Windows의 PC 보호" 파란 창이 뜨면 **추가 정보 → 실행**을 누르세요. (서명하지 않은 앱이라 뜨는 안내입니다.)

### Mac에서 처음 열 때
1. zip을 풀고 `DartSimpleInvest.app`을 **응용 프로그램** 폴더로 옮깁니다.
2. 더블클릭 → "확인되지 않은 개발자" 경고가 뜨면 **완료**를 누릅니다.
3. **시스템 설정 → 개인정보 보호 및 보안** 맨 아래의 **그래도 열기**를 누르고 암호를 입력합니다.
4. 다음부터는 그냥 더블클릭하면 열립니다.

"손상되었기 때문에 열 수 없습니다"가 뜨면 터미널에서 한 번만 실행하세요.
```
xattr -cr /Applications/DartSimpleInvest.app
```

## 사용법

1. API 키(opendart.fss.or.kr에서 발급), 기간, 검색어, 저장 폴더를 확인하고 **▶ 시작**을 누릅니다. 설정은 다음 실행 때도 남아 있습니다.
2. 상태등을 보세요.
   - 🟢 **정상 작동 중** — 기다리면 됩니다. 진행 막대와 남은 시간이 나옵니다.
   - 🟡 **응답 지연** — 90초 넘게 DART 응답이 없음. 잠시 기다려 보세요.
   - 🔴 **응답 없음** — 3분 넘게 응답이 없음. **■ 중지** 후 다시 **▶ 시작**하세요.
3. 3단계 분석은 인터넷을 쓰지 않아서 '마지막 응답'이 오래돼도 정상입니다.
4. 끝나면 **엑셀 열기**를 누르세요. 다 받지 못했으면 `_부분.xlsx`로 저장됩니다.

- 받은 원문은 저장 폴더의 `dart_cache`에 남아서, 중지하거나 창을 닫아도 다음에 **이어서** 받습니다.
- 작업 중에는 컴퓨터가 잠자기로 들어가지 않게 막아 둡니다(화면은 꺼져도 됨). 노트북은 뚜껑을 닫지 마세요.
- 진행 기록은 저장 폴더의 `실행기록.txt`에도 남습니다.

## 명령줄 버전

```
python -m pip install -r requirements.txt
python dart_simple_invest.py     # 파일 위쪽의 API_KEY, START, END를 먼저 고치세요
```
30초마다 `♥` 생존 신호 줄이 찍힙니다. `dart_engine.py`와 같은 폴더에 두어야 합니다.

## 직접 실행·빌드 (개발용)

```
python dart_app.py               # 앱 실행
python dart_app.py --selftest    # 분석·엑셀 저장 점검
pip install pyinstaller && pyinstaller --noconfirm dart_app.spec   # dist/ 에 앱 생성
```
Mac 앱은 Mac에서, Windows 앱은 Windows에서만 만들 수 있어서 GitHub Actions가 대신 만듭니다.
