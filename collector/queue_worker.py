"""웹 '실시간 조회' 요청 처리기 — Mac launchd 가 5분마다 실행한다.

흐름: query_requests(pending) 1건 가져오기 → 키워드로 커뮤니티 검색 → 관련성/제외 필터
     → 기간 필터 → Claude 분류 → items 저장 + 요청에 결과 연결 → done.

설계 메모
- 분류 기준(현대카드 소비자여론)을 유지하기 위해 검색어는 '현대카드 {키워드}' 로 만든다.
  (입력에 이미 현대카드/현카가 있으면 그대로 사용)
- 앱스토어 리뷰·X 는 키워드 검색이 아니므로 제외. 대신 기존 DB 아카이브에서 웹이 바로 찾는다.
- 데일리 생성 잡(main.py)이 돌고 있으면 사이트 부담·충돌을 피하려고 이번 회차는 건너뛴다.
- 한 번 실행에 최대 MAX_PER_RUN 건만 처리(저빈도 원칙).
"""
import datetime
import subprocess
import sys

import config  # noqa: F401  (.env 로드)
import classify
import collectors
import db

MAX_PER_RUN = 2
SKIP_SOURCES = {"구글플레이", "앱스토어", "X"}


def _daily_running():
    try:
        r = subprocess.run(["pgrep", "-if", r"python main\.py"], capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


def _search_terms(keywords):
    out = []
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        if "현대카드" in kw or "현카" in kw:
            out.append(kw)
        else:
            out.append(f"현대카드 {kw}")
    return out


def process(req):
    terms = _search_terms(req["keywords"])
    print(f"[조회 #{req['id']}] {terms} {req['date_from']}~{req['date_to']}")
    enabled = [n for n in collectors.COLLECTORS if n not in SKIP_SOURCES]
    items, errors = collectors.collect_all(terms, enabled=enabled)
    items, _ = collectors.filter_relevant(items)

    d_from, d_to = req["date_from"], req["date_to"]
    kept = []
    for it in items:
        d = collectors.parse_post_date(it.get("date"))
        if isinstance(d, datetime.datetime):
            d = d.date()
        if d is None:
            it["date_unknown"], it["_pdate"] = True, None
            kept.append(it)  # 날짜 미상은 버리지 않는다(웹에서 '날짜미상' 표기)
        elif d_from <= d <= d_to:
            it["date_unknown"], it["_pdate"] = False, d
            kept.append(it)

    # 같은 링크 중복 제거
    seen, uniq = set(), []
    for it in kept:
        if it.get("link") and it["link"] not in seen:
            seen.add(it["link"])
            uniq.append(it)

    classify.annotate(uniq)
    uniq = [it for it in uniq if it.get("relevant", True)
            and it.get("type") not in ("뉴스", "채용")]
    ids = db.upsert_items(uniq, origin="query")
    return ids, errors


def main():
    if not db.enabled():
        print("[조회] DATABASE_URL 미설정 — 종료")
        return
    if _daily_running():
        print("[조회] 데일리 생성 잡 실행 중 — 이번 회차 건너뜀")
        return
    db.requeue_stale(60)
    for _ in range(MAX_PER_RUN):
        req = db.claim_query()
        if not req:
            break
        classify.reset_stats()
        run_id = db.run_start("query")
        try:
            ids, errors = process(req)
            db.finish_query(req["id"], "done", item_ids=ids)
            db.run_finish(run_id, "success", sources=collectors.SOURCE_STATS,
                          claude=classify.STATS,
                          summary={"query_id": req["id"], "keywords": req["keywords"],
                                   "results": len([i for i in ids if i]), "errors": errors})
            print(f"[조회 #{req['id']}] 완료 — {len([i for i in ids if i])}건")
        except Exception as e:
            db.finish_query(req["id"], "failed", error=f"{type(e).__name__}: {e}")
            db.run_finish(run_id, "failed", sources=collectors.SOURCE_STATS,
                          claude=classify.STATS, error=f"{type(e).__name__}: {e}")
            print(f"[조회 #{req['id']}] 실패: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
