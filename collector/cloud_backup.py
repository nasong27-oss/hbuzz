"""클라우드 백업 수집 — 집 Mac 이 실패했을 때 Vercel(서울 리전)에서 대신 리포트를 만든다.

Mac 파이프라인(main.run_pipeline)을 그대로 쓰되 다음만 다르다.
  - 수집: 사이트별 스레드 병렬 + 전체 마감시간. 마감까지 못 끝낸 사이트도 그때까지 모은 건 살린다.
  - 대상: 최근 'vercel-icn1' 진단에서 차단/접속불가로 나온 사이트는 처음부터 건너뛴다.
  - 기준 데이터: 중복 제거·챗봇 이력·뉴스 중복은 로컬 파일 대신 DB.
  - 표시: 제목 '[버즈리포트 백업]', 리포트 '수집 문제'에 누락 사이트 명시.
  - 안전: 같은 날 집 Mac 리포트가 이미 있으면 저장·발송하지 않는다.
"""
import concurrent.futures as cf
import os
import threading
import time

os.environ.setdefault("HBUZZ_DATA_DIR", "/tmp/hbuzz")

import config  # noqa: E402,F401
import classify  # noqa: E402
import collectors  # noqa: E402
import db  # noqa: E402

ENV = "vercel-icn1"
COLLECT_DEADLINE = 110  # 초. Vercel Hobby 함수 최대 300초 안에 분류·리포트까지 끝내기 위함
ALWAYS_SKIP = {"X"}
NAVER_CLOUD_PAGES = 2   # 집 Mac 은 최대 10페이지
BAD = {"blocked", "unreachable", "timeout"}


def _reset_markers():
    for attr in dir(collectors):
        v = getattr(collectors, attr)
        if attr.endswith("_FETCHED"):
            if isinstance(v, set):
                v.clear()
            elif isinstance(v, bool):
                setattr(collectors, attr, False)


def pick_sources():
    """진단 결과로 클라우드에서 시도할 사이트와 건너뛸 사이트를 정한다."""
    names = [n for n in collectors.COLLECTORS if n not in ALWAYS_SKIP]
    diag = db.latest_diagnosis(ENV)
    if not diag:
        return names, {}, False
    res = diag.get("results", {})
    use = [n for n in names if res.get(n, {}).get("status") not in BAD]
    skipped = {n: f"클라우드 진단에서 {res[n].get('status')} ({res[n].get('detail', '')})"[:160]
               for n in names if n not in use}
    return use, skipped, True


def make_collector(sources, deadline=None):
    """main.run_pipeline(collect=...) 에 넘길 수집 함수를 만든다."""

    def collect(keywords):
        limit = deadline or COLLECT_DEADLINE
        _reset_markers()
        # 네이버카페는 변형 10개를 다 돈 뒤에야 결과를 돌려주므로, 마감 안에 끝나게 페이지를 줄인다
        collectors.NAVER_MAX_PAGES = min(collectors.NAVER_MAX_PAGES, NAVER_CLOUD_PAGES)
        collectors.SOURCE_STATS.clear()
        lock = threading.Lock()
        buckets = {n: [] for n in sources}
        meta = {n: {"t0": time.time(), "done": False, "error": "", "kw": 0} for n in sources}
        stop = threading.Event()

        def work(name):
            fn = collectors.COLLECTORS[name]
            for kw in keywords:
                if stop.is_set():
                    break
                try:
                    got = fn(kw) or []
                except Exception as e:  # noqa: BLE001
                    meta[name]["error"] = f"{type(e).__name__}: {e}"[:200]
                    got = []
                with lock:
                    buckets[name].extend(got)
                    meta[name]["kw"] += 1
            meta[name]["done"] = True

        ex = cf.ThreadPoolExecutor(max_workers=max(1, len(sources)))
        futs = [ex.submit(work, n) for n in sources]
        cf.wait(futs, timeout=limit)
        stop.set()  # 남은 스레드는 다음 키워드로 넘어가지 않게
        ex.shutdown(wait=False, cancel_futures=True)

        items, errors = [], {}
        with lock:
            for n in sources:
                merged = collectors._dedup_by_url(buckets[n])
                items.extend(merged)
                m = meta[n]
                secs = round(time.time() - m["t0"], 1)
                note = m["error"] or ("" if m["done"] else
                                      f"마감({limit}s) — 키워드 {m['kw']}/{len(keywords)}개까지만")
                collectors.SOURCE_STATS[n] = {"count": len(merged), "secs": secs, "tries": 1,
                                              "error": note}
                if not merged:
                    errors[n] = note or "0건 — 클라우드 수집 실패 추정"
                elif not m["done"]:
                    errors[n] = note
                print(f"[클라우드 수집] {n}: {len(merged)}건 {note}")
        return items, errors

    return collect


def run_backup(send=False, force=False):
    """백업 리포트를 만든다. 반환: 요약 dict (웹에 그대로 보여준다)."""
    import datetime
    import main

    today = datetime.date.today()
    if db.primary_report_exists(today) and not force:
        return {"ok": False, "reason": "오늘 집 Mac 리포트가 이미 있어 백업을 실행하지 않았습니다."}

    sources, skipped, had_diag = pick_sources()
    extra = {"백업 수집": "집 Mac 대신 클라우드(Vercel 서울)에서 수집한 리포트입니다. 일부 사이트가 빠졌을 수 있습니다."}
    extra.update(skipped)
    if not had_diag:
        extra["진단 없음"] = "클라우드 진단 기록이 없어 모든 사이트를 시도했습니다."

    db.apply_runtime_settings()  # 모델은 관리자 설정을 따른다
    os.environ["CLASSIFIER"] = "api"  # 클라우드에는 구독 로그인이 없어 경로 설정과 무관하게 API 키
    classify.reset_stats()
    run_id = db.run_start("cloud")
    t0 = time.time()
    try:
        stats = main.run_pipeline(collect=make_collector(sources), cloud=True, send=send,
                                  extra_errors=extra)
    except Exception as e:
        db.run_finish(run_id, "failed", sources=collectors.SOURCE_STATS, claude=classify.STATS,
                      error=f"{type(e).__name__}: {e}")
        raise
    stats = dict(stats or {})
    stats.update({"elapsed": round(time.time() - t0, 1), "sources_tried": sources,
                  "sources_skipped": list(skipped)})
    db.run_finish(run_id, "success", sources=collectors.SOURCE_STATS, claude=classify.STATS,
                  summary=stats)
    ok_sources = [n for n, s in collectors.SOURCE_STATS.items() if s.get("count")]
    return {"ok": stats.get("skipped") != "primary_exists", "report_date": str(today),
            "stats": stats, "ok_sources": ok_sources,
            "failed_sources": [n for n in sources if n not in ok_sources] + list(skipped)}


def run_diagnosis():
    import diag
    rep = diag.run(ENV, timeout=200)
    diag.save_db(rep)
    return rep
