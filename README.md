# hbuzz — 현대카드 데일리 온라인 버즈

현대카드에 대한 국내 커뮤니티·앱스토어 여론을 매일 아침 수집하고 Claude로 분류해 메일로 보내는
모니터링 파이프라인, 그리고 그 결과를 조회·관리·발송하는 웹 대시보드.

`allroundPark/dashboard`를 기반으로 이관하면서 바뀐 점:

- 분류기 Gemini → **Claude Sonnet 5.5** (본인 Anthropic API 키, tool use 구조화 출력)
- 발신 메일 **nasong27@gmail.com**
- **웹 대시보드**(Vercel): 최근 7일 리포트, 키워드·기간 조회와 발송, 수신자·키워드 관리, 운영 패널
- 집 Mac **원클릭 설치 스크립트**, 실행 전 GitHub 자동 업데이트

---

## 구조

```
 집 Mac (가정용 IP — 국내 사이트 수집 가능)            Vercel (서울 리전)
 ┌───────────────────────────────────┐            ┌──────────────────────────┐
 │ 07:30 run_generate.sh             │            │ Next.js 대시보드          │
 │   수집 → Claude 분류 → 리포트 생성 │──저장──┐    │  /          7일 리포트     │
 │ 08:00 run_send.sh → 메일 발송      │        │    │  /search    조회·발송      │
 │ 5분마다 run_queue.sh               │        ▼    │  /admin     수신자·키워드  │
 │   웹 수집 요청 처리                │◀─ Neon Postgres ─▶│            발송이력·운영  │
 └───────────────────────────────────┘            └──────────────────────────┘
```

수집은 클라우드에서 할 수 없다(국내 커뮤니티가 데이터센터 IP를 차단). 그래서 수집·분류·정기 발송은
집 Mac이, 조회·관리·즉석 발송은 웹이 맡고 둘은 Neon DB를 공유한다. DB가 죽어도 Mac 쪽 리포트·발송은
멈추지 않는다.

| 폴더 | 내용 |
|---|---|
| `app/`, `components/`, `lib/` | 웹 대시보드 (Next.js 16, App Router) |
| `db/schema.sql` | DB 스키마 (웹·Mac 공용) |
| `collector/` | 수집기 (Python, 집 Mac에서 실행) |

---

## 처음 설정 (순서대로)

### 1. Vercel 프로젝트 만들기 — 아이패드 가능

1. [vercel.com/new](https://vercel.com/new)에서 `nasong27-oss/hbuzz` Import → Deploy
   (프레임워크는 Next.js로 자동 인식, 별도 설정 없음)
2. 프로젝트 > **Storage** > Create Database > **Neon** 선택 → 프로젝트에 연결.
   `DATABASE_URL`이 환경변수에 자동으로 들어간다.
3. 프로젝트 > Settings > **Environment Variables**에 추가:

   | 키 | 값 |
   |---|---|
   | `SITE_PASSWORD` | 팀 공용 비밀번호 |
   | `ADMIN_PASSWORD` | 관리자 비밀번호 (다르게) |
   | `AUTH_SECRET` | 32자 이상 아무 랜덤 문자열 |
   | `GMAIL_APP_PASSWORD` | nasong27@gmail.com 앱 비밀번호 16자리 |

4. Deployments > 최신 배포 > **Redeploy** (환경변수 반영)
5. 사이트 접속 → 관리자 비밀번호로 로그인 → 관리 > 운영 > **DB 초기화** → **테스트 메일 보내기**
6. 관리 > 수신자, 관리 > 키워드 등록

이후 `main` 브랜치에 push 할 때마다 자동 배포된다.

> Gmail 앱 비밀번호: Google 계정 > 보안 > 2단계 인증 켜기 > 앱 비밀번호 생성.

### 2. 집 Mac 설정 — 맥 앞에서 1회

터미널에 그대로 붙여넣기:

```bash
git clone https://github.com/nasong27-oss/hbuzz.git ~/hbuzz && ~/hbuzz/collector/install_mac.sh
```

스크립트가 차례로 물어본다: Gmail 앱 비밀번호, Anthropic API 키, Neon `DATABASE_URL`
(Vercel > Storage > Neon > `.env.local` 탭에서 복사). 끝나면 launchd 3개가 등록되고, 잠자기 해제·정전 후
자동 부팅을 적용할지 묻는다. **전원 어댑터를 꽂아 두면 된다.**

바로 시험:

```bash
cd ~/hbuzz/collector && .venv/bin/python main.py --dry-run --no-dedupe
```

웹 리포트 화면에 오늘 날짜가 나타나면 연결 성공.

> 비공개 레포라면 Mac에서 `gh auth login` 또는 GitHub 계정 로그인이 한 번 필요하다(자동 업데이트용 `git pull`).

---

## 웹 기능

- **리포트** — 최근 7일 감성 막대(부정·중립·긍정), 최신 리포트의 주요 이슈와 부정 여론, 날짜별 발송 여부.
  날짜를 누르면 게시글 전체(감성·카테고리 필터), 업계 뉴스, 수집 문제, 받은 메일 원본, 발송 기록, 다시 보내기.
- **조회·발송** — 키워드(여러 개)·기간·감성·카테고리·앱 관련으로 쌓인 게시글 검색 → 결과를 메일로 발송.
  아직 모이지 않은 키워드는 **새로 수집 요청** → 집 Mac이 5분 안에 '현대카드 + 키워드'로 검색·분류해 결과를 붙인다.
- **관리**(관리자만)
  - 수신자: 추가·이름·켜기/끄기·아침 메일 여부·삭제. 아침 리포트는 여기 등록된 사람에게 간다(비어 있으면 Mac `.env`)
  - 키워드: 아침 수집 검색어(비어 있으면 Mac `.env`)
  - 발송 이력: 아침 자동·조회 결과·재발송 전부
  - 운영: 경고, 웹 설정 상태, 최근 실행(사이트별 건수·소요시간, Claude 호출·토큰·폴백)

권한: 팀 비밀번호는 조회와 **등록된 수신자에게만** 발송, 관리자는 임의 주소 발송과 관리 기능까지.
메일은 BCC로 나가 수신자끼리 주소가 보이지 않는다.

### 운영 경고 (대시보드 상단)

- 08:05가 지났는데 오늘 아침 발송 기록이 없음 → 리포트 화면에서 재발송 가능
- 마지막 리포트 생성 실패
- AI 분류 실패로 키워드 규칙이 쓰인 글이 있음 (API 키·한도 확인)
- 수집 요청이 15분 넘게 대기 → 집 Mac 꺼짐·잠자기 의심

---

## 집 Mac 일상 운영

| 하고 싶은 것 | 방법 |
|---|---|
| 수신자·키워드 변경 | 웹 관리 화면 (Mac 손댈 필요 없음) |
| 코드 수정 | GitHub에 push → 다음 실행 때 Mac이 자동 반영 |
| 로그 보기 | `~/hbuzz/collector/data/launchd.log`, `queue.log` |
| 잡 확인 | `launchctl list \| grep hbuzz` |
| 비밀값 변경 | `nano ~/hbuzz/collector/.env` |
| 재설치·재등록 | `~/hbuzz/collector/install_mac.sh` (다시 실행해도 안전) |

수집기 세부 동작·장애 기록은 [`collector/HANDOFF.md`](collector/HANDOFF.md),
[`collector/INCIDENTS.md`](collector/INCIDENTS.md).

---

## 비용

- Claude API: 하루 1회 분류(수십~수백 건) + 수집 요청 처리분. 실제 사용량은 관리 > 운영의 토큰 수로 확인.
  모델은 Mac `.env`의 `CLAUDE_MODEL`로 바꿀 수 있다.
- Vercel·Neon·Gmail: 무료 티어 범위.

## 개발

```bash
npm install && cp .env.example .env.local   # 값 채우기
npm run dev
cd collector && python -m unittest discover tests   # DATABASE_URL 있으면 통합 테스트 포함
```
