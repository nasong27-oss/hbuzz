-- hbuzz — Neon Postgres 스키마.
-- 웹(Next.js)과 수집기(Mac, Python)가 함께 쓴다. 여러 번 실행해도 안전(IF NOT EXISTS).
-- 적용: 웹 어드민 > 운영 > "DB 초기화" 버튼, 또는 Mac 에서 `python db.py init`.

-- 날짜별 데일리 리포트 (Mac 생성 잡이 저장)
CREATE TABLE IF NOT EXISTS reports (
  id              SERIAL PRIMARY KEY,
  report_date     DATE NOT NULL UNIQUE,
  subject         TEXT NOT NULL,
  html            TEXT NOT NULL,
  text_body       TEXT NOT NULL,
  issue_summary   JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {"부정":[...],"긍정":[...],"중립":[...]}
  stats           JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 건수·카테고리별·폴백 등
  errors          JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 사이트별 수집 실패
  keywords        TEXT[] NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 수집·분류된 게시글 아카이브 (표시용: 비속어 마스킹 적용본)
CREATE TABLE IF NOT EXISTS items (
  id            BIGSERIAL PRIMARY KEY,
  link          TEXT NOT NULL UNIQUE,
  source        TEXT NOT NULL,
  title         TEXT NOT NULL,
  snippet       TEXT NOT NULL DEFAULT '',
  post_date     DATE,                     -- 게시일(파싱 실패 시 NULL)
  date_unknown  BOOLEAN NOT NULL DEFAULT false,
  sentiment     TEXT NOT NULL DEFAULT '중립',
  category      TEXT NOT NULL DEFAULT '기타',
  type          TEXT NOT NULL DEFAULT '소비자여론',
  app_related   BOOLEAN NOT NULL DEFAULT false,
  reason        TEXT NOT NULL DEFAULT '',
  score         SMALLINT NOT NULL DEFAULT 0,
  fallback      BOOLEAN NOT NULL DEFAULT false,  -- 키워드 폴백 분류 여부
  origin        TEXT NOT NULL DEFAULT 'daily',   -- daily | query
  report_date   DATE,                     -- 해당 날짜 리포트에 실렸으면 그 날짜
  collected_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS items_post_date_idx ON items (post_date);
CREATE INDEX IF NOT EXISTS items_collected_idx ON items (collected_at);
CREATE INDEX IF NOT EXISTS items_report_date_idx ON items (report_date);

-- 리포트에 실린 금융 뉴스
CREATE TABLE IF NOT EXISTS news (
  id           BIGSERIAL PRIMARY KEY,
  report_date  DATE NOT NULL,
  title        TEXT NOT NULL,
  link         TEXT NOT NULL,
  press        TEXT NOT NULL DEFAULT '',
  is_foreign   BOOLEAN NOT NULL DEFAULT false,
  UNIQUE (report_date, link)
);

-- 수신자 (어드민에서 관리. 데일리 발송은 active AND receive_daily)
CREATE TABLE IF NOT EXISTS recipients (
  id             SERIAL PRIMARY KEY,
  email          TEXT NOT NULL UNIQUE,
  name           TEXT NOT NULL DEFAULT '',
  active         BOOLEAN NOT NULL DEFAULT true,
  receive_daily  BOOLEAN NOT NULL DEFAULT true,
  note           TEXT NOT NULL DEFAULT '',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 모니터링 키워드 (어드민에서 관리. 비어 있으면 Mac .env KEYWORDS 사용)
CREATE TABLE IF NOT EXISTS keywords (
  id          SERIAL PRIMARY KEY,
  keyword     TEXT NOT NULL UNIQUE,
  active      BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 발송 이력 (데일리 자동 + 웹 수동)
CREATE TABLE IF NOT EXISTS send_logs (
  id           BIGSERIAL PRIMARY KEY,
  kind         TEXT NOT NULL,              -- daily | manual | resend
  subject      TEXT NOT NULL,
  recipients   TEXT[] NOT NULL DEFAULT '{}',
  status       TEXT NOT NULL,              -- success | failed
  error        TEXT,
  report_date  DATE,
  query        JSONB,                      -- 수동 발송 시 조회 조건
  item_count   INT NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS send_logs_created_idx ON send_logs (created_at DESC);

-- 실행 이력 (운영 헬스 패널)
CREATE TABLE IF NOT EXISTS runs (
  id           BIGSERIAL PRIMARY KEY,
  kind         TEXT NOT NULL,              -- generate | send | query
  status       TEXT NOT NULL,              -- running | success | failed
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ,
  sources      JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {사이트: {count, secs, tries, error}}
  claude       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {calls, fallback, input_tokens, output_tokens, errors}
  summary      JSONB NOT NULL DEFAULT '{}'::jsonb,
  error        TEXT
);
CREATE INDEX IF NOT EXISTS runs_started_idx ON runs (started_at DESC);

-- 웹 실시간 조회 요청 큐 (Mac 이 5분 주기로 처리)
CREATE TABLE IF NOT EXISTS query_requests (
  id            BIGSERIAL PRIMARY KEY,
  keywords      TEXT[] NOT NULL,
  date_from     DATE NOT NULL,
  date_to       DATE NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending',   -- pending | running | done | failed
  result_count  INT NOT NULL DEFAULT 0,
  error         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS query_requests_status_idx ON query_requests (status, created_at);

CREATE TABLE IF NOT EXISTS query_request_items (
  query_id  BIGINT NOT NULL REFERENCES query_requests(id) ON DELETE CASCADE,
  item_id   BIGINT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  PRIMARY KEY (query_id, item_id)
);

-- 실행 환경별 사이트 수집 진단 결과 (집 Mac / Vercel 서울 / GitHub Actions)
CREATE TABLE IF NOT EXISTS diagnostics (
  id       BIGSERIAL PRIMARY KEY,
  env      TEXT NOT NULL,
  ran_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  results  JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS diagnostics_env_idx ON diagnostics (env, ran_at DESC);
