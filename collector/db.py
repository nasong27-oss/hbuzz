"""Neon Postgres 연동 — 웹 대시보드와 데이터를 공유한다.

원칙: DB 는 '보조 경로'다. DATABASE_URL 이 없거나 DB 가 죽어 있어도
수집·리포트 생성·메일 발송은 절대 막히지 않는다(모든 함수가 예외를 삼키고 로그만 남김).

  python db.py init      # 스키마 생성(여러 번 실행해도 안전)
  python db.py check     # 연결 확인
"""
import datetime
import json
import os
import sys
import traceback

import config

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:  # psycopg 미설치여도 파이프라인은 돈다
    psycopg = None
    Jsonb = None

SCHEMA_PATH = os.path.join(os.path.dirname(config.BASE_DIR), "db", "schema.sql")


def enabled():
    return bool(config.DATABASE_URL and psycopg)


def _connect():
    return psycopg.connect(config.DATABASE_URL, connect_timeout=15, autocommit=True)


def _warn(where, e):
    print(f"[DB] {where} 실패(무시하고 진행): {type(e).__name__}: {e}")


def _safe(where, default=None):
    """DB 함수 데코레이터 — 비활성/실패 시 default 반환."""
    def deco(fn):
        def wrapper(*a, **kw):
            if not enabled():
                return default
            try:
                return fn(*a, **kw)
            except Exception as e:  # noqa: BLE001
                _warn(where, e)
                return default
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


def init_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        sql = f.read()
    with _connect() as conn:
        conn.execute(sql)
    print("[DB] 스키마 적용 완료")


# ---------------------------------------------------------------------------
# 설정값(수신자·키워드) — DB 우선, 실패/비어있으면 .env 폴백
# ---------------------------------------------------------------------------

@_safe("키워드 조회", default=None)
def active_keywords():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT keyword FROM keywords WHERE active ORDER BY id").fetchall()
    return [r[0] for r in rows] or None


@_safe("수신자 조회", default=None)
def daily_recipients():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT email FROM recipients WHERE active AND receive_daily ORDER BY id").fetchall()
    return [r[0] for r in rows] or None


def resolve_keywords():
    kws = active_keywords()
    if kws:
        print(f"[DB] 키워드 {len(kws)}개 사용(DB)")
        return kws
    return config.KEYWORDS


def resolve_recipients():
    rcpt = daily_recipients()
    if rcpt:
        return rcpt
    return config.REPORT_TO


# 관리자 분류 설정 — 웹 관리 > 분류 설정. 허용값만 받는다(웹과 같은 목록).
CLASSIFIER_MODES = ("auto", "claude-code", "api")
CLAUDE_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")


@_safe("설정 조회", default={})
def get_settings():
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {k: v for k, v in rows}


def apply_runtime_settings():
    """DB 설정을 이번 실행에 적용한다. DB 가 없거나 값이 이상하면 .env 값을 그대로 둔다."""
    s = get_settings() or {}
    mode, model = s.get("classifier_mode"), s.get("claude_model")
    used = []
    if mode in CLASSIFIER_MODES:
        os.environ["CLASSIFIER"] = mode
        used.append("경로")
    if model in CLAUDE_MODELS:
        config.CLAUDE_MODEL = model
        used.append("모델")
    print(f"[설정] 분류 경로={os.environ.get('CLASSIFIER', 'auto')} 모델={config.CLAUDE_MODEL}"
          + (f" (DB: {', '.join(used)})" if used else " (.env)"))


# ---------------------------------------------------------------------------
# 실행 이력(runs)
# ---------------------------------------------------------------------------

@_safe("실행 시작 기록", default=None)
def run_start(kind):
    with _connect() as conn:
        row = conn.execute(
            "INSERT INTO runs (kind, status) VALUES (%s, 'running') RETURNING id", (kind,)
        ).fetchone()
    return row[0]


@_safe("실행 종료 기록")
def run_finish(run_id, status, sources=None, claude=None, summary=None, error=None):
    if not run_id:
        return
    with _connect() as conn:
        conn.execute(
            """UPDATE runs SET status=%s, finished_at=now(), sources=%s, claude=%s,
                   summary=%s, error=%s WHERE id=%s""",
            (status, Jsonb(sources or {}), Jsonb(claude or {}), Jsonb(summary or {}),
             (error or None) and str(error)[:2000], run_id),
        )


# ---------------------------------------------------------------------------
# 게시글·리포트 저장
# ---------------------------------------------------------------------------

def _item_row(it, origin):
    pdate = it.get("_pdate")
    if isinstance(pdate, datetime.datetime):
        pdate = pdate.date()
    return (
        it.get("link"), it.get("source", ""), it.get("title", ""), it.get("snippet", "") or "",
        pdate if isinstance(pdate, datetime.date) else None,
        bool(it.get("date_unknown")),
        it.get("sentiment", "중립"), it.get("category", "기타"), it.get("type", "소비자여론"),
        bool(it.get("app_related")), it.get("reason", "") or "", int(it.get("score") or 0),
        bool(it.get("fallback")), origin,
    )


_UPSERT_ITEM = """
INSERT INTO items (link, source, title, snippet, post_date, date_unknown, sentiment, category,
                   type, app_related, reason, score, fallback, origin)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (link) DO UPDATE SET
  title=EXCLUDED.title, snippet=EXCLUDED.snippet,
  post_date=COALESCE(EXCLUDED.post_date, items.post_date),
  sentiment=EXCLUDED.sentiment, category=EXCLUDED.category, type=EXCLUDED.type,
  app_related=EXCLUDED.app_related, reason=EXCLUDED.reason, score=EXCLUDED.score,
  fallback=EXCLUDED.fallback
RETURNING id
"""


def _mask(it):
    import mask
    out = dict(it)
    for k in ("title", "snippet", "reason"):
        out[k] = mask.mask_profanity(out.get(k, "") or "")
    return out


@_safe("게시글 저장", default=[])
def upsert_items(items, origin="daily"):
    """표시용(마스킹) 사본을 upsert. 반환: 입력 순서대로 item id 리스트."""
    ids = []
    with _connect() as conn:
        with conn.transaction():
            for it in items:
                if not it.get("link"):
                    ids.append(None)
                    continue
                row = conn.execute(_UPSERT_ITEM, _item_row(_mask(it), origin)).fetchone()
                ids.append(row[0])
    print(f"[DB] 게시글 {len([i for i in ids if i])}건 저장")
    return ids


@_safe("리포트 저장", default=False)
def save_report(report_date, subject, html, text_body, issue_summary, stats, errors,
                keywords, report_links, news):
    with _connect() as conn:
        with conn.transaction():
            conn.execute(
                """INSERT INTO reports (report_date, subject, html, text_body, issue_summary,
                                         stats, errors, keywords)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (report_date) DO UPDATE SET subject=EXCLUDED.subject,
                     html=EXCLUDED.html, text_body=EXCLUDED.text_body,
                     issue_summary=EXCLUDED.issue_summary, stats=EXCLUDED.stats,
                     errors=EXCLUDED.errors, keywords=EXCLUDED.keywords, updated_at=now()""",
                (report_date, subject, html, text_body, Jsonb(issue_summary or {}),
                 Jsonb(stats or {}), Jsonb(errors or {}), list(keywords or [])),
            )
            if report_links:
                conn.execute("UPDATE items SET report_date=%s WHERE link = ANY(%s)",
                             (report_date, list(report_links)))
            for n in news or []:
                conn.execute(
                    """INSERT INTO news (report_date, title, link, press, is_foreign)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT (report_date, link) DO NOTHING""",
                    (report_date, n.get("title", ""), n.get("link", ""), n.get("press", ""),
                     bool(n.get("foreign"))),
                )
    print(f"[DB] 리포트 {report_date} 저장")
    return True


@_safe("발송 이력 기록")
def log_send(kind, subject, recipients, status, error=None, report_date=None, item_count=0):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO send_logs (kind, subject, recipients, status, error, report_date,
                                       item_count) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (kind, subject, list(recipients or []), status, error and str(error)[:2000],
             report_date, item_count),
        )


# ---------------------------------------------------------------------------
# 클라우드 백업용 조회 (로컬 파일 대신 DB 를 기준으로)
# ---------------------------------------------------------------------------

@_safe("최근 리포트 링크 조회", default=[])
def recent_report_links(days=21):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT link FROM items WHERE report_date >= current_date - %s", (days,)).fetchall()
    return [r[0] for r in rows]


@_safe("최근 뉴스 조회", default=[])
def recent_news_titles(days=14):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT title FROM news WHERE report_date >= current_date - %s", (days,)).fetchall()
    return [r[0] for r in rows]


@_safe("챗봇 최근일 조회", default=None)
def latest_chatbot_date():
    with _connect() as conn:
        row = conn.execute(
            "SELECT max(COALESCE(post_date, collected_at::date))::text FROM items "
            "WHERE category='챗봇'").fetchone()
    return row[0] if row else None


@_safe("리포트 존재 확인", default=False)
def primary_report_exists(report_date):
    """백업이 아닌(집 Mac) 리포트가 그 날짜에 이미 있는지."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM reports WHERE report_date=%s "
            "AND COALESCE((stats->>'backup')::boolean, false) = false", (report_date,)).fetchone()
    return bool(row)


@_safe("최근 진단 조회", default=None)
def latest_diagnosis(env):
    with _connect() as conn:
        row = conn.execute(
            "SELECT results FROM diagnostics WHERE env=%s ORDER BY ran_at DESC LIMIT 1",
            (env,)).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# 조회 요청 큐
# ---------------------------------------------------------------------------

@_safe("조회 요청 가져오기", default=None)
def claim_query():
    """가장 오래된 pending 요청 1건을 running 으로 바꾸며 가져온다(동시 처리 방지)."""
    with _connect() as conn:
        row = conn.execute(
            """UPDATE query_requests SET status='running', started_at=now()
               WHERE id = (SELECT id FROM query_requests WHERE status='pending'
                           ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
               RETURNING id, keywords, date_from, date_to"""
        ).fetchone()
    if not row:
        return None
    return {"id": row[0], "keywords": list(row[1]), "date_from": row[2], "date_to": row[3]}


@_safe("멈춘 조회 요청 복구")
def requeue_stale(minutes=60):
    """Mac 이 처리 도중 꺼진 running 요청을 다시 pending 으로."""
    with _connect() as conn:
        conn.execute(
            """UPDATE query_requests SET status='pending', started_at=NULL
               WHERE status='running' AND started_at < now() - make_interval(mins => %s)""",
            (minutes,))


@_safe("조회 요청 완료 기록")
def finish_query(qid, status, item_ids=None, error=None):
    ids = [i for i in (item_ids or []) if i]
    with _connect() as conn:
        with conn.transaction():
            for iid in ids:
                conn.execute(
                    """INSERT INTO query_request_items (query_id, item_id) VALUES (%s,%s)
                       ON CONFLICT DO NOTHING""", (qid, iid))
            conn.execute(
                """UPDATE query_requests SET status=%s, finished_at=now(), result_count=%s,
                       error=%s WHERE id=%s""",
                (status, len(ids), error and str(error)[:2000], qid))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if not config.DATABASE_URL:
        print(".env 에 DATABASE_URL 이 없습니다.")
        sys.exit(1)
    if psycopg is None:
        print("psycopg 미설치: .venv/bin/pip install -r requirements.txt")
        sys.exit(1)
    try:
        if cmd == "init":
            init_schema()
        else:
            with _connect() as conn:
                v = conn.execute("SELECT version()").fetchone()[0]
            print("[DB] 연결 OK —", v.split(",")[0])
    except Exception:
        traceback.print_exc()
        sys.exit(1)
