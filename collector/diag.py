"""사이트별 수집 가능 여부 진단 — 실행 환경(집 Mac / Vercel 서울 / GitHub Actions)마다 돌려 비교한다.

각 사이트를 '현대카드' 1회 검색으로만 찔러본다(재시도 없음, 사이트별 병렬).
수집기는 오류를 삼키고 0건을 돌려주는 경우가 많아, requests 호출을 가로채
실제 HTTP 상태/예외를 함께 기록해 '0건'이 차단인지 진짜 0건인지 구분한다.

  python diag.py --env home-mac                 # 표로 출력
  python diag.py --env github-actions --out r.json
  python diag.py --env home-mac --save-db       # 웹 운영 탭에 저장
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import sys
import threading
import time
from urllib.parse import urlsplit

import config  # noqa: F401  (.env 로드)
import collectors

SKIP = {"X"}  # 로그인 필요·상시 실패라 진단 의미 없음
_local = threading.local()


def _record(kind, url, status=None, error=None):
    log = getattr(_local, "log", None)
    if log is not None:
        log.append({"host": urlsplit(url).hostname or "", "status": status,
                    "error": error and f"{type(error).__name__}: {error}"[:160]})


def _install_recorder():
    """requests.get/post 를 감싸 스레드별로 HTTP 결과를 남긴다(수집 로직은 그대로)."""
    req = collectors.requests
    if getattr(req, "_hbuzz_wrapped", False):
        return
    orig_get, orig_post = req.get, req.post

    def wrap(fn, kind):
        def inner(url, *a, **kw):
            try:
                r = fn(url, *a, **kw)
            except Exception as e:
                _record(kind, url, error=e)
                raise
            _record(kind, url, status=r.status_code)
            return r
        return inner

    req.get, req.post = wrap(orig_get, "GET"), wrap(orig_post, "POST")
    req._hbuzz_wrapped = True


def _verdict(count, calls, exc):
    statuses = [c["status"] for c in calls if c["status"] is not None]
    errors = [c["error"] for c in calls if c["error"]]
    blocked = [s for s in statuses if s in (401, 403, 429, 451)]
    if count > 0:
        return "ok", ""
    if not calls and not exc:
        return "unknown", "요청 기록 없음(외부 라이브러리 경로이거나 비활성 수집기)"
    if blocked:
        return "blocked", f"HTTP {blocked[0]}"
    if any(s and s >= 400 for s in statuses):
        return "error", f"HTTP {[s for s in statuses if s >= 400][0]}"
    if errors and not statuses:
        return "unreachable", errors[0]
    if exc:
        return "error", exc
    if errors:
        return "error", errors[0]
    return "empty", "응답은 정상, 결과 0건(구조 변경 또는 실제 0건)"


def _probe(name, fn, keyword):
    _local.log = []
    t0 = time.time()
    items, exc = [], None
    try:
        items = fn(keyword) or []
    except Exception as e:  # noqa: BLE001
        exc = f"{type(e).__name__}: {e}"[:200]
    calls = _local.log
    _local.log = None
    status, detail = _verdict(len(items), calls, exc)
    ages = []
    today = datetime.date.today()
    for it in items:
        d = collectors.parse_post_date(it.get("date"))
        if isinstance(d, datetime.datetime):
            d = d.date()
        if d:
            ages.append((today - d).days)
    return {
        "status": status,
        "detail": detail,
        "count": len(items),
        "secs": round(time.time() - t0, 1),
        "requests": len(calls),
        "http": sorted({c["status"] for c in calls if c["status"] is not None}),
        "newest_days": min(ages) if ages else None,
    }


def _news_probe(_kw):
    q, edition = collectors.NEWS_FEEDS[0]
    return collectors._fetch_news_feed(q, edition)


def run(env, keyword="현대카드", timeout=90, workers=8, sources=None):
    _install_recorder()
    targets = {n: f for n, f in collectors.COLLECTORS.items() if n not in SKIP}
    targets["구글뉴스"] = _news_probe
    if sources:
        targets = {n: f for n, f in targets.items() if n in sources}
    # 일부 수집기는 '실행당 1회' 마커가 있어 초기화해 둔다
    for attr in dir(collectors):
        if attr.endswith("_FETCHED") and isinstance(getattr(collectors, attr), bool):
            setattr(collectors, attr, False)

    results = {}
    started = time.time()
    ex = cf.ThreadPoolExecutor(max_workers=workers)
    futs = {ex.submit(_probe, n, f, keyword): n for n, f in targets.items()}
    done, pending = cf.wait(futs, timeout=timeout)
    for f in done:
        results[futs[f]] = f.result()
    for f in pending:
        results[futs[f]] = {"status": "timeout", "detail": f"{timeout}초 안에 끝나지 않음",
                            "count": 0, "secs": timeout, "requests": 0, "http": [],
                            "newest_days": None}
    ex.shutdown(wait=False, cancel_futures=True)
    return {
        "env": env,
        "ran_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "keyword": keyword,
        "elapsed": round(time.time() - started, 1),
        "results": dict(sorted(results.items())),
    }


def save_db(report):
    import db
    if not db.enabled():
        print("[진단] DATABASE_URL 없음 — DB 저장 건너뜀")
        return False
    with db._connect() as conn:
        conn.execute("INSERT INTO diagnostics (env, ran_at, results) VALUES (%s, %s, %s)",
                     (report["env"], report["ran_at"], db.Jsonb(report)))
    print("[진단] DB 저장 완료")
    return True


LABEL = {"unknown": "판단불가", "ok": "수집됨", "blocked": "차단", "unreachable": "접속불가", "error": "오류",
         "empty": "0건", "timeout": "시간초과"}


def print_table(report):
    print(f"\n[{report['env']}] {report['ran_at']} ({report['elapsed']}초)")
    for name, r in report["results"].items():
        print(f"  {name:<10} {LABEL.get(r['status'], r['status']):<5} {r['count']:>4}건 "
              f"{r['secs']:>5}s http={r['http']} {r['detail']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--out")
    ap.add_argument("--save-db", action="store_true")
    ap.add_argument("--timeout", type=int, default=150)
    a = ap.parse_args()
    rep = run(a.env, timeout=a.timeout)
    print_table(rep)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    if a.save_db:
        save_db(rep)
    sys.exit(0)
