# 인수인계 (HANDOFF)

> **2026-10 이관 메모**: 이 문서는 `allroundPark/dashboard` 시절 기준이다. 현재 레포는
> `nasong27-oss/hbuzz`, 수집기는 `~/hbuzz/collector`, launchd 라벨은 `com.hbuzz.*`,
> 설치는 `collector/install_mac.sh` 한 번으로 끝난다. 전체 구조는 루트 [README](../README.md).

이 문서는 **데일리 온라인 버즈**를 다른 사람이 이어받아 운영·수정할 수 있도록 정리한
실무 가이드다. 개요·아키텍처는 [README.md](README.md), 실제 발생한 장애와 원인·조치는
[INCIDENTS.md](INCIDENTS.md) 참고.

---

## 1. 현재 운영 상태

- **어디서**: 특정 macOS Mac 로컬(`~/hyundaibuzz`). ☁️ 클라우드는 국내 사이트 프록시
  차단(403)이라 **불가** — 반드시 이 로컬 Mac에서 돈다.
- **언제**: 매일 **07:30 생성 → 08:00 발송** (launchd 2개 잡).
- **누구에게**: `.env`의 `REPORT_TO` (회사 메일 다수). `product2team1@gmail.com`은
  **검증계**로 분리 — 자동 발송에서 제외, 수동 테스트에만 사용.
- **비용**: Claude API 사용량(본인 키) + Gmail SMTP·Neon·Vercel 무료 티어.

---

## 2. ⚙️ macOS 최초 설정 (터미널) — 새 Mac에 올릴 때 반드시

> 이 설정들이 없으면 **뚜껑 닫으면 안 돌거나, 기상 직후 네트워크 실패로 메일이 안 온다.**

### (1) 위치 — `~/Documents` 금지
프로젝트는 **홈 바로 아래**(`~/hyundaibuzz`)에 둔다. `~/Documents`/`~/Desktop`은
macOS TCC 보호 대상이라 launchd가 `Operation not permitted`(exit 126)로 죽는다.

### (2) 파이썬 환경
```bash
cd ~/hyundaibuzz
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # 값 채우기(GMAIL_APP_PASSWORD, ANTHROPIC_API_KEY, DATABASE_URL ...)
```

### (3) 잠자기 방지 — 닫아도 돌게
```bash
sudo pmset -a disablesleep 1          # 뚜껑 닫아도 sleep 안 함
pmset -g | grep -i SleepDisabled      # → 'SleepDisabled  1' 이면 정상
```
- **AC 전원에 연결**해 둘 것(배터리로 두면 방전/절전 위험). `pmset -g batt` 로 확인.
- 각 실행은 래퍼에서 `caffeinate -s -i` 로 한 번 더 잠자기를 막는다(자동).
- ⚠️ 보안: 뚜껑 닫아도 켜져 있으므로 화면잠금·물리적 접근에 유의.

### (4) launchd 잡 등록
```bash
cp com.hyundaibuzz.*.plist ~/Library/LaunchAgents/    # (리포에 없으면 아래 시각으로 생성)
launchctl load ~/Library/LaunchAgents/com.hyundaibuzz.generate.plist
launchctl load ~/Library/LaunchAgents/com.hyundaibuzz.send.plist
launchctl list | grep hyundaibuzz                     # 두 잡이 보이면 등록됨
```
- generate = **07:30**(`Hour 7 / Minute 30`), send = **08:00**(`Hour 8 / Minute 0`).
- **자가치유(2026-09-16 장애 후 도입)**: 발송 잡은 오늘자 리포트가 없으면 그냥 끝내지 않는다.
  ① 생성 잡이 아직 돌고 있으면 최대 40분 대기(30초 폴링) → 끝나는 대로 발송,
  ② 생성 프로세스도 없고 리포트도 없으면 발송 잡이 **직접 `main.py --dry-run` 실행 후 발송**.
  생성 잡의 네트워크 대기는 10분(기상 직후 Wi-Fi 지연 대비). 자세한 경위는 INCIDENTS.md.
- plist 수정 후엔 반드시 `launchctl unload` → `load` 로 다시 읽힌다.
- (레거시) `com.hyundaibuzz.daily.plist`(07:00)는 **사용 안 함** — 로드하지 말 것.

### (5) 권한(TCC)
launchd가 `Operation not permitted`로 죽으면 **시스템 설정 > 개인정보 보호 및 보안 >
전체 디스크 접근**에 `/bin/bash`(또는 터미널)를 추가한다. 위 (1)처럼 홈 아래에 두면
대부분 불필요하다.

### (6) 수신 측 화이트리스트
회사 보안메일이 첫 수신 발신자를 스팸/격리로 보낼 수 있다. 새 수신자는 최초 1~2회
**스팸/격리함 확인 + 발신자(`product2team1@gmail.com`) 허용** 안내가 필요하다.

---

## 3. 자주 하는 변경

모두 로컬 `~/hyundaibuzz`에서. 코드 수정은 GitHub에 커밋/푸시, `.env`는 로컬만.

### 수신자 추가/제거
`.env`의 `REPORT_TO` 를 쉼표로 편집(런타임에 읽으므로 재시작 불필요).
```bash
# 확인
.venv/bin/python -c "import config; print(config.REPORT_TO)"
```

### 키워드 추가
`.env`의 `KEYWORDS` 편집. 네이버 카페 검색 변형은 `collectors.py`의
`_CAFE_QUERY_VARIANTS` 도 함께 손본다.

### 최근 윈도우 조정 (며칠치 볼지)
`main.py` 상단 `RECENT_DAYS`(기타 2) / `PAY_DAYS`(결제 7) / `CHATBOT_DAYS`(챗봇 14) /
`APP_REVIEW_DAYS`(앱리뷰 2). 앱 리뷰가 저볼륨이라 자주 비면 `APP_REVIEW_DAYS`를 늘린다.

### 디시 갤러리 추가 (아사모처럼 특정 갤 직접검색)
`collectors.py`의 `_DC_GALLERIES` 리스트에 `(갤러리id, "표시라벨", 마이너여부)` 추가.

### 제외 필터 (부채/영업 등)
`collectors.py`의 `_EXCLUDE_SOURCE_RE`(카페/갤명) / `_EXCLUDE_TEXT_RE`(제목+요약).
부분문자열 오탐(예: '회생'↔'사회생활')에 주의 — debt 동반어를 요구하도록 좁게.

### 분류 기준(결제/챗봇/앱/감성)
`classify.py`의 Claude 프롬프트 문자열을 수정. 폴백 키워드는 같은 파일 하단.

---

## 4. 수동 실행 / 검증

```bash
# 메일 없이 생성만
.venv/bin/python main.py --dry-run

# 검증계(gmail)에만 전체 그림으로 테스트
REPORT_TO="product2team1@gmail.com" .venv/bin/python main.py --no-dedupe

# 특정 수신자 조합으로 테스트
REPORT_TO="product2team1@gmail.com, someone@hyundaicard.com" .venv/bin/python main.py --no-dedupe
```
`--no-dedupe` = 이전에 본 글도 포함(전체 그림). 평소 데일리는 `seen.json`으로 새 글만.

---

## 5. 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| 메일이 안 옴 | ① 스팸/격리함 확인 ② `data/launchd.log` 확인 ③ `GMAIL_APP_PASSWORD` 만료 시 재발급 |
| 8시에 안 왔는데 낮에 실행하면 옴 | 기상 직후 네트워크 레이스 — 래퍼의 네트워크 대기가 처리하지만, AC/絶전 상태 확인 |
| 특정 사이트 0건 | 리포트 "수집 실패 추정"에 뜨면 사이트 구조 변경 가능 → 해당 수집기 셀렉터 점검 |
| 전부 0건 | 네트워크/차단. `launchd.log`의 `[수집]` 라인 확인 |
| 분류가 이상/누락 | API 키·사용량 한도 문제 시 키워드 폴백(품질 저하). 웹 관리 > 운영에서 오류 확인 |
| `Operation not permitted`(126) | TCC — 2-(5) 전체 디스크 접근, 또는 홈 아래로 이동 |
| 앱 섹션 자주 빔 | 앱 리뷰 저볼륨(며칠에 몇 건) + 2일 윈도우. `APP_REVIEW_DAYS` 조정 |

**로그 위치**: `data/launchd.log`(자동 실행), `data/gmail_test_run*.log`(수동 테스트).

---

## 6. 알려진 한계

- **더쿠**: 비로그인 통합검색 차단 → 항상 0건(수집 실패로 표기됨). 로그인 세션 붙이기 전 복구 불가.
- **X(트위터)**: 신규 계정 로그인 챌린지(code 399)로 대부분 실패 — 베스트에포트(없어도 됨).
- **앱스토어**: 애플 amp-api 쓰로틀링 시 0건(20s 타임아웃으로 행 방지).
- **클라우드 불가**: Anthropic 클라우드 프록시가 국내 사이트 403 → 로컬 Mac 전용.
- **네이버/디시 정렬**: `start`/`/p/N/` 파라미터가 있어야 날짜순 정상화됨(코드에 반영됨).

---

## 7. 데이터 & 백업

- `data/archive.jsonl` — 모든 항목 누적(추이 분석용, 원본 유지=비속어 마스킹 전).
- `data/seen.json` — 중복 방지(본 링크 기록, 최근 N개 유지).
- 위 `data/`·`reports/`·`.env`는 **`.gitignore`로 커밋 제외** — 로컬 Mac에만 존재.
- **소스코드**만 GitHub `allroundPark/dashboard`(main)에 커밋/푸시.
  `.env`(앱 비밀번호·API 키·수신자)는 **절대 커밋 금지**.

---

## 8. 연락 / 원칙

- 무료 유지(유료 벤더 금지) · 저빈도 프론트도어 조회(봇 회피 기법 금지) ·
  공개 게시글의 제목·요약·링크만 수집.
- 실제 개발은 신중히, 사이트 구조 변경 시 셀렉터부터 점검.
