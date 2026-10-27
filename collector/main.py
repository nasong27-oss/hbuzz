"""데일리 온라인 버즈 — 수집 → 중복 제거 → 긍부정 → 리포트 → 발송.

사용법:
  python main.py            # 수집 후 메일 발송 (설정돼 있으면)
  python main.py --dry-run  # 메일 없이 reports/ 에 HTML만 저장
  python main.py --no-dedupe  # 이전에 본 글도 모두 포함 (테스트용)
"""
import datetime
import json
import os
import sys

import config
import collectors
import classify
import report
import mailer
import mask
import db

SEEN_PATH = os.path.join(config.DATA_DIR, "seen.json")
MAX_SEEN = 5000  # 링크 기록 상한
SEEN_NEWS_PATH = os.path.join(config.DATA_DIR, "seen_news.json")  # 이미 내보낸 뉴스 key(겹침 방지)
MAX_SEEN_NEWS = 800
ARCHIVE_PATH = os.path.join(config.DATA_DIR, "archive.jsonl")  # 분류된 항목 이력(추이 분석용)


def _valid_ymd(s):
    """문자열이 유효한 YYYY-MM-DD 날짜면 그 값을 그대로 반환, 아니면 None.
    (fromisoformat 로 엄격 검증 — 2026-08-06 형태만 통과)."""
    if not s:
        return None
    s = str(s).strip()[:10]
    try:
        datetime.date.fromisoformat(s)
        return s
    except (ValueError, TypeError):
        return None


def latest_chatbot_date(path=ARCHIVE_PATH):
    """data/archive.jsonl 을 훑어 category == "챗봇" 행 중 가장 최근 날짜(YYYY-MM-DD 문자열)를 반환.

    - 각 줄을 JSON 으로 파싱(깨진 줄은 건너뜀).
    - 우선 행의 `date` 필드를 YYYY-MM-DD 로 검증, 비었거나 파싱 불가면
      `collected_at` 의 날짜 부분(앞 10자)으로 폴백.
    - 유효한 날짜들 중 최댓값을 반환. 챗봇 행이 없거나 파일이 없으면 None.
    - 파일 없음/빈 파일/깨진 줄에도 절대 예외를 내지 않는다."""
    best = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(row, dict) or row.get("category") != "챗봇":
                    continue
                d = _valid_ymd(row.get("date")) or _valid_ymd(row.get("collected_at"))
                if d and (best is None or d > best):
                    best = d
    except OSError:
        return None
    return best


def archive_items(items):
    """분류된 항목을 data/archive.jsonl 에 한 줄씩 append 한다(누적 이력).
    dedup(seen.json)으로 새 항목만 넘어오므로 같은 글이 중복 적재되지 않는다."""
    if not items:
        return
    run_ts = datetime.datetime.now().isoformat(timespec="seconds")
    fields = ("source", "title", "link", "snippet", "date", "sentiment",
              "category", "app_related", "type", "reason", "date_unknown")
    with open(ARCHIVE_PATH, "a", encoding="utf-8") as f:
        for it in items:
            row = {k: it.get(k) for k in fields}
            row["collected_at"] = run_ts
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

# 리포트 대상 창(시간축 피드): '지금 기준 최근 N일'을 싣는다(오늘 포함).
#  카테고리별로 윈도우가 다르다(팀장 핵심 주제인 챗봇·결제는 넓게, 나머지는 좁게).
#  실제 윈도우 판정은 분류(category) 이후에 한다 — 그래야 챗봇/결제 글이
#  분류 전에 날짜로 조용히 버려지지 않는다.
RECENT_DAYS = 2       # 기타(일반) — 오늘·어제만
APP_REVIEW_DAYS = 7   # 앱 리뷰는 저볼륨 + 애플 amp-api 스로틀링으로 매일 안 들어옴 →
                      # 최근 한 주로 넓혀 수집됐을 때 리뷰가 리포트에 보이게 한다.
CHATBOT_DAYS = 14     # 챗봇(헨리) 관련 글은 저볼륨이라 더 넓은 윈도우(팀장 핵심 주제)
PAY_DAYS = 7          # 결제 관련 글도 핵심 주제라 넓게

# 분류 전 '거친' 사전 드롭 기준: 가장 넓은 카테고리 윈도우.
#  category 를 모르는 시점이라도, 어떤 카테고리 윈도우도 이 값을 넘지 않으므로
#  MAX_WINDOW 보다 오래된 글은 분류해봐야 최종 윈도우에서 어차피 버려진다.
#  → 미리 드롭해 Claude 분류 볼륨을 안전하게 제한한다(날짜 미상은 유지).
MAX_WINDOW = max(RECENT_DAYS, APP_REVIEW_DAYS, CHATBOT_DAYS, PAY_DAYS)


def _category_window(it):
    """분류(category)와 출처를 보고 이 글에 적용할 날짜 윈도우(일수)를 정한다.

    - category == "챗봇" → CHATBOT_DAYS(14일)  [앱 리뷰보다 우선 — 챗봇 앱리뷰도 넓게 잡음]
    - 앱 리뷰(구글플레이/앱스토어)는 그 외엔 APP_REVIEW_DAYS(7일).
    - category == "결제" → PAY_DAYS(7일)
    - 그 외(기타) → RECENT_DAYS(2일)
    """
    # 앱 리뷰(구글플레이/앱스토어)는 category 와 무관하게 항상 최근 윈도우.
    #  (챗봇으로 분류된 오래된 리뷰가 챗봇 윈도우로 새어들어오는 것을 막는다.)
    if it.get("source") in collectors.APP_REVIEW_SOURCES:
        return APP_REVIEW_DAYS
    cat = it.get("category")
    if cat == "챗봇":          # 커뮤니티 챗봇 글만 넓게(저볼륨·팀장 핵심)
        return CHATBOT_DAYS
    if cat == "결제":
        return PAY_DAYS
    return RECENT_DAYS


def load_seen():
    if os.path.exists(SEEN_PATH):
        with open(SEEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_seen(seen):
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen[-MAX_SEEN:], f, ensure_ascii=False)


def load_seen_news():
    if os.path.exists(SEEN_NEWS_PATH):
        with open(SEEN_NEWS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_seen_news(keys):
    with open(SEEN_NEWS_PATH, "w", encoding="utf-8") as f:
        json.dump(keys[-MAX_SEEN_NEWS:], f, ensure_ascii=False)


def main():
    """실행 이력(runs)을 DB에 남기며 파이프라인을 돌린다. DB 실패는 파이프라인을 막지 않는다."""
    classify.reset_stats()
    run_id = db.run_start("generate")
    try:
        summary = run_pipeline()
    except Exception as e:
        db.run_finish(run_id, "failed", sources=collectors.SOURCE_STATS,
                      claude=classify.STATS, error=f"{type(e).__name__}: {e}")
        raise
    db.run_finish(run_id, "success", sources=collectors.SOURCE_STATS,
                  claude=classify.STATS, summary=summary)


def run_pipeline():
    dry_run = "--dry-run" in sys.argv
    no_dedupe = "--no-dedupe" in sys.argv

    keywords = db.resolve_keywords()
    items, errors = collectors.collect_all(keywords)

    # 1) 관련성 사전 필터: '현대카드'(공백 허용) 또는 '현카'가 실제로 나오는 글만 남긴다.
    items, dropped_irrelevant = collectors.filter_relevant(items)

    # 1.5) 거친 사전 날짜 드롭: 분류 볼륨을 제한하기 위한 '넓은' 윈도우 필터.
    #  - 여기서는 category 를 아직 모르므로 가장 넓은 MAX_WINDOW(=30일) 기준으로만 자른다.
    #  - MAX_WINDOW 보다 오래된 확정 날짜 → DROP (어떤 카테고리 윈도우에서도 어차피 탈락)
    #  - 날짜 미상(파싱 실패/빈 값/애매) → keep 하되 date_unknown 표시 (절대 조용히 버리지 않음)
    #  - 확정 날짜는 _pdate 에 저장해 분류 후 카테고리별 윈도우 판정에서 재사용한다.
    # 보수 원칙: '확실히 MAX_WINDOW 밖'인 경우에만 드롭한다. 파싱 실패는 드롭하지 않는다.
    today = datetime.date.today()
    kept, dropped_coarse = [], 0
    for it in items:
        d = collectors.parse_post_date(it.get("date"))
        if d is None:
            it["date_unknown"] = True
            it["_pdate"] = None
            kept.append(it)
            continue
        it["date_unknown"] = False
        it["_pdate"] = d
        age = (today - d).days
        if age >= MAX_WINDOW:  # 확실히 가장 넓은 윈도우보다도 오래됨 → 분류 전 드롭
            dropped_coarse += 1
        else:
            kept.append(it)  # 미래(음수)·최근은 유지 → 최종 카테고리 윈도우에서 판정
    items = kept

    # 2) 링크 기준 중복 제거 (실행 간 + 실행 내)
    seen = load_seen()
    seen_set = set(seen)
    new_items, batch = [], set()
    for it in items:
        link = it.get("link", "")
        if not link or link in batch:
            continue
        if not no_dedupe and link in seen_set:
            continue
        batch.add(link)
        new_items.append(it)

    # 3) Claude Haiku 분류 (실패 시 sentiment.py 키워드 분류로 폴백)
    #  분류 대상은 '거친 30일 사전 드롭 + 중복 제거'를 거친 집합이다(코스트/볼륨 가드).
    n_classified = len(new_items)
    classify.annotate(new_items)

    # 3.5) 앱 스토어 리뷰(구글플레이/앱스토어)는 정의상 '현대카드 앱'에 대한 소비자여론이다.
    #  리뷰 본문에 '현대카드'가 없어 분류기가 relevant=False/app_related=False 로 볼 수 있으므로,
    #  relevant·app_related·type 을 강제로 고정해 📱 앱 관련 여론 섹션과 앱 집계에 반드시 실리게 한다.
    #  (감정/사유는 Claude 판단 결과를 그대로 유지 — 리뷰 텍스트 기반이라 가치가 있다.)
    for it in new_items:
        if it.get("source") in collectors.APP_REVIEW_SOURCES:
            it["relevant"] = True
            it["app_related"] = True
            it["type"] = "소비자여론"

    # 4) 분류기가 '현대카드가 아니다'로 본 항목 제거 (부동산 경매 등 부수적 언급 포함)
    new_items = [it for it in new_items if it.get("relevant", True)]

    # 4.1) 뉴스(언론기사)·채용(구인) 유형은 소비자 여론이 아니므로 리포트에서 제외(사용자 요청).
    new_items = [it for it in new_items if it.get("type") not in ("뉴스", "채용")]

    # 4.2) 아카이브: 윈도우로 좁히기 전, 관련성 있는 새 항목 전체를 이력에 남긴다.
    archive_items(new_items)
    db.upsert_items(new_items, origin="daily")  # 웹 대시보드용(마스킹 사본)

    # 4.5) 카테고리 인지 날짜 윈도우 (분류 이후에 적용).
    #  - 이제 category 를 알므로 챗봇(30일)/결제(7일)/앱리뷰(7일)/기타(2일)를 각각 적용한다.
    #  - date_unknown(파싱 실패) → 항상 유지 (절대 조용히 버리지 않음).
    #  - 확정 날짜가 자기 카테고리 윈도우를 벗어나거나 미래면 DROP.
    kept, dropped_old = [], 0
    kept_by_cat = {}  # 콘솔 출력용 카테고리별 유지 건수
    for it in new_items:
        if it.get("date_unknown"):
            kept.append(it)
            kept_by_cat["날짜미상"] = kept_by_cat.get("날짜미상", 0) + 1
            continue
        window = _category_window(it)
        age = (today - it["_pdate"]).days
        if 0 <= age < window:  # age 0=오늘, 1=어제 … / 음수(미래)는 제외
            kept.append(it)
            label = "앱리뷰" if it.get("source") in collectors.APP_REVIEW_SOURCES \
                else it.get("category", "기타")
            kept_by_cat[label] = kept_by_cat.get(label, 0) + 1
        else:
            dropped_old += 1
    new_items = kept

    new_items.sort(key=lambda x: x["score"])  # 부정 먼저

    # 5) 오늘의 이슈 요약(소비자여론을 긍/부정/중립으로 묶은 짧은 불릿) — Claude 1회, 실패 시 폴백
    consumer_for_summary = [
        i for i in new_items if i.get("type", "소비자여론") == "소비자여론"
    ]
    issue_summary = classify.summarize_issues(consumer_for_summary)

    # 6) 표시용 비속어 마스킹(제목·요약·사유). 아카이브는 위(4.2)에서 원본으로 이미 저장됨.
    for it in new_items:
        it["title"] = mask.mask_profanity(it.get("title", ""))
        it["snippet"] = mask.mask_profanity(it.get("snippet", ""))
        it["reason"] = mask.mask_profanity(it.get("reason", ""))
    issue_summary = {k: [mask.mask_profanity(s) for s in v]
                     for k, v in issue_summary.items()} if isinstance(issue_summary, dict) else issue_summary

    # 6.5) 챗봇 섹션은 항상 표시(팀장 핵심 주제). 오늘 챗봇 0건이면 '가장 최근 이력' 날짜를
    #  아카이브에서 뽑아 함께 보여준다. 마스킹 이후에 계산해 표시 파이프라인을 건드리지 않는다.
    chatbot_last_date = latest_chatbot_date()

    # 6.6) 결제·챗봇 중심 금융 뉴스(소비자 여론과 별개, 리포트 앞 별도 섹션). 실패해도 무시.
    #  이미 내보낸 뉴스(seen_news)는 제외해 날짜가 넘어가도 겹치지 않게 한다.
    seen_news = load_seen_news()
    news = collectors.finance_news(seen=set(seen_news))
    if news:
        print(f"금융 뉴스 {len(news)}건 수집")
        if not no_dedupe:  # 테스트(--no-dedupe) 실행은 seen_news 를 오염시키지 않는다.
            save_seen_news(seen_news + [n["key"] for n in news])

    # 분류 폴백이 있었으면 리포트에 드러낸다(과거 Gemini 장애 때 조용히 품질 저하된 교훈).
    if classify.STATS["fallback"]:
        errors = dict(errors)
        errors["Claude 분류"] = (f"키워드 폴백 {classify.STATS['fallback']}건 — 분류 품질 저하 가능"
                               + (f" ({classify.STATS['errors'][0]})" if classify.STATS["errors"] else ""))

    html_body = report.build_html(new_items, errors, keywords, issue_summary,
                                  chatbot_last_date=chatbot_last_date, news=news)
    text_body = report.build_text(new_items, errors, keywords, issue_summary,
                                  chatbot_last_date=chatbot_last_date, news=news)
    today = datetime.date.today().strftime("%Y-%m-%d")
    out_path = os.path.join(config.REPORT_DIR, f"{today}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_body)
    txt_path = os.path.join(config.REPORT_DIR, f"{today}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text_body)
    consumer = [i for i in new_items if i.get("type") == "소비자여론"]
    n_app = sum(1 for i in consumer if i.get("app_related"))
    cat_order = ["챗봇", "결제", "앱리뷰", "기타", "날짜미상"]
    cat_str = " / ".join(
        f"{k} {kept_by_cat[k]}건" for k in cat_order if kept_by_cat.get(k)
    )
    print(
        f"관련 글(오탐 {dropped_irrelevant}건 제외, 30일밖 {dropped_coarse}건 사전드롭) "
        f"→ 분류 {n_classified}건 → 카테고리 윈도우 밖 {dropped_old}건 제외 "
        f"→ 최종 {len(new_items)}건 "
        f"[{cat_str}] "
        f"(소비자여론 {len(consumer)}건, 앱관련 {n_app}건), 리포트 저장: {out_path}"
    )
    if errors:
        print("수집 실패:", errors)

    neg = sum(1 for i in consumer if i["sentiment"] == "부정")
    subject = f"[버즈리포트] {today} — 소비자여론 {len(consumer)}건 (부정 {neg})"
    stats = {
        "final": len(new_items), "consumer": len(consumer), "negative": neg,
        "positive": sum(1 for i in consumer if i["sentiment"] == "긍정"),
        "app": n_app, "by_category": kept_by_cat, "classified": n_classified,
        "dropped_irrelevant": dropped_irrelevant, "dropped_old": dropped_old,
        "fallback": classify.STATS["fallback"], "news": len(news or []),
        "model": config.CLAUDE_MODEL,
    }
    if not no_dedupe:  # 테스트 실행은 DB 리포트를 덮어쓰지 않는다
        db.save_report(today, subject, html_body, text_body, issue_summary, stats, errors,
                       keywords, [it["link"] for it in new_items if it.get("link")], news)

    if not dry_run:
        to = db.resolve_recipients()
        try:
            mailer.send(subject, html_body, text_body, to=to)
            print("메일 발송 완료 →", ", ".join(to))
            db.log_send("daily", subject, to, "success", report_date=today,
                        item_count=len(new_items))
        except Exception as e:
            print("메일 발송 실패:", e)
            db.log_send("daily", subject, to, "failed", error=e, report_date=today)

    if not no_dedupe:
        save_seen(seen + [it["link"] for it in new_items])
    return stats


if __name__ == "__main__":
    main()
