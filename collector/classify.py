"""Claude(Anthropic Messages API) 기반 긍부정 + 유형 분류.

각 게시글을 요청당 여러 건(기본 15건) 묶어 Claude에 보내고, 항목별로 채운다:
  - relevant    (bool) : 실제로 신용카드사 '현대카드'에 관한 글인가
  - type        (str)  : 소비자여론 / 뉴스 / 채용 / 가십기타
  - category    (str)  : 결제 / 챗봇 / 기타
  - sentiment   (str)  : 긍정 / 부정 / 중립
  - app_related (bool) : 현대카드 '모바일 앱'에 관한 글인가
  - reason      (str)  : 판단 근거 한 문장 (한국어)
  - score       (int)  : 정렬용. 부정=-1, 중립=0, 긍정=1

응답은 tool use(강제 도구 호출)로 받아 JSON 스키마를 보장한다 — 텍스트 파싱 실패가 없다.
SDK 의존성을 늘리지 않으려고 requests 로 REST 를 직접 호출한다.

ANTHROPIC_API_KEY 가 없거나 호출이 실패하면 sentiment.py 키워드 분류로
graceful fallback 하므로 파이프라인이 절대 hard-fail 하지 않는다.
폴백이 발생하면 STATS["fallback"] 에 집계돼 리포트·대시보드에 표시된다.
"""
import json

import time

import requests

import config
import sentiment

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
BATCH_SIZE = 15  # 요청당 분류 건수
REQUEST_TIMEOUT = 90
# 네트워크·레이트리밋·일시 서버오류는 키워드 폴백으로 떨어뜨리기 전에 재시도한다.
API_RETRIES = 4
API_BACKOFF = 5  # 초 (5,10,15)

# 실행 단위 통계 — main.py 가 읽어 run 로그(DB)와 리포트에 남긴다.
STATS = {"calls": 0, "fallback": 0, "input_tokens": 0, "output_tokens": 0, "errors": []}


def reset_stats():
    STATS.update({"calls": 0, "fallback": 0, "input_tokens": 0, "output_tokens": 0, "errors": []})


def call_claude(system, prompt, tool_name, tool_desc, schema, max_tokens=4096):
    """도구 호출을 강제해 schema 에 맞는 dict 를 돌려준다. 실패 시 예외."""
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY 미설정")
    body = {
        "model": config.CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system,
        "tools": [{"name": tool_name, "description": tool_desc, "input_schema": schema}],
        "tool_choice": {"type": "tool", "name": tool_name},
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    last_err = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            STATS["calls"] += 1
            resp = requests.post(API_URL, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
            # 429(레이트리밋)·529(과부하)·5xx 는 일시적 → 재시도
            if resp.status_code in (429, 500, 502, 503, 504, 529):
                raise requests.HTTPError(f"{resp.status_code} 일시적 오류")
            if resp.status_code >= 400:
                # 401/403/400 등은 재시도 무의미 → 바로 실패
                raise RuntimeError(f"{resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            usage = data.get("usage") or {}
            STATS["input_tokens"] += int(usage.get("input_tokens") or 0)
            STATS["output_tokens"] += int(usage.get("output_tokens") or 0)
            for block in data.get("content") or []:
                if block.get("type") == "tool_use" and block.get("name") == tool_name:
                    return block.get("input") or {}
            raise ValueError("tool_use 블록 없음")
        except RuntimeError as e:
            last_err = e
            break
        except (requests.RequestException, ValueError) as e:
            last_err = e
            if attempt < API_RETRIES:
                time.sleep(API_BACKOFF * attempt)
    msg = f"{type(last_err).__name__}: {last_err}"
    if msg not in STATS["errors"]:
        STATS["errors"].append(msg[:300])
    raise last_err


VALID_TYPES = {"소비자여론", "뉴스", "채용", "가십기타"}
VALID_CATEGORIES = {"결제", "챗봇", "기타"}
VALID_SENTIMENTS = {"긍정", "부정", "중립"}

_SCORE = {"부정": -1, "중립": 0, "긍정": 1}

SYSTEM_PROMPT = (
    "너는 한국 커뮤니티 게시글이 신용카드사 '현대카드'에 대한 소비자 여론인지 "
    "분류하는 분석가다. 결과는 반드시 지정한 도구로 제출한다."
)

PROMPT_TEMPLATE = """다음은 커뮤니티에서 '현대카드'로 검색된 게시글 목록이다. 각 항목을 분류하라.

각 게시글에 대해 아래 필드를 판단한다:
- relevant: 신용카드사 '현대카드'(카드/혜택/서비스/앱/고객센터 등)에 관한 글이면 true. 동명이인·다른 회사·단순 오탐이면 false.
    또한 **현대카드가 부수적으로만 언급되고 글의 주제가 다른 것**은 false 로 한다:
    부동산 경매/매물·낙찰·감정가 글, 일반 재테크/주식/대출 글, 자동차 매매 등 → 현대카드 소비자 이슈가 아니면 false.
- type: 다음 중 하나
    - "소비자여론": 카드/혜택/서비스/발급/앱 등에 대한 일반 사용자의 의견·경험·불만·후기
    - "뉴스": 언론 기사, 보도자료성 글
    - "채용": 채용·아르바이트·상담직 문의 등 근무/구인 관련 (예: FDS 상담 알바 문의)
    - "가십기타": 임원/유명인 근황, 가십, 소비자 여론이 아닌 잡담
- category: 글의 주제를 다음 중 하나로 분류
    - "결제": 현대카드로 **실제 지불(결제)하는 행위**와 **결제수단** 관련 이슈만.
        (돈 얘기라고 다 결제가 아니다 — 아래 '결제 아님'은 반드시 "기타"로 보낸다.)
        · 결제수단/간편결제(특정 수단에 치우치지 말 것 — 아래는 전부 동등하게 '결제'다):
          앱카드(현대카드 앱카드 결제)·PIN(핀)번호결제/비밀번호결제·일반결제(카드 긁기·온라인 카드결제)·
          삼성페이·애플페이·네이버페이·카카오페이·터치결제/컨택리스·오프라인 단말기 등으로 현대카드 결제
        · 결제 행위/오류: 온라인결제, 오프라인 매장 결제, 자동결제, 해외결제, 결제 승인/거절/취소/실패/오류,
          "결제가 안 된다/막힌다", 승인문자(결제 승인 알림)
        · 결제 아님(→반드시 "기타"): 한도(상향/하향), 할부/무이자할부, 청구서·명세서·청구금액, 연회비,
          발급/신청/심사, 혜택/할인/적립/M포인트 적립·사용, 이벤트/쿠폰, 카드 디자인, 분실/재발급, 상담/챗봇,
          **방문추심·추심·독촉, 개인회생·회생·파산, 채무·빚·연체** (채무/추심/회생 관련은 결제 행위가 아니라 기타)
        · **쇼핑/핫딜/상품 게시글도 결제 아님(→기타)**: 상품·특가·구매후기가 주제이고 현대카드가
          '싸게 사는 할인 결제수단'으로만 언급된 글. 예) "[핫딜] …", "[CJ온스타일] 운동화 (현카 53,940원)",
          "네이버 현대카드로 무이자할부 상품구매", 상품 가격/특가 공유. → 결제는 '결제 자체의 문제/경험/수단
          이슈'일 때만이다(결제 오류·거절·안됨, 삼성/애플페이 사용성 등). 상품이 주인공이면 기타.
    - "챗봇": 현대카드 AI 챗봇 '헨리'(Henry)/상담봇/AI상담/챗봇 문의 등 챗봇 관련.
        (주의: '헨리 코튼' 같은 의류 브랜드 '헨리'는 챗봇이 아니다 → 기타)
    - "기타": 그 외(발급/혜택/이벤트/디자인/한도/할부/청구/적립/분실 등 결제행위·챗봇이 아닌 모든 것)
- sentiment: "긍정" / "부정" / "중립" (현대카드에 대한 태도)
- app_related: **현대카드 메인 앱('현대카드' 앱 = 앱카드 앱)** 자체에 관한 글이면 true.
    로그인·모바일 화면·업데이트·오류·버그·UI/UX·푸시알림·앱 결제기능 등 '메인 앱' 사용 이야기면 true.
    단, **'더 라운지(the Lounge)'·DIVE·myTicket·현대카드 부티크 등 현대카드의 별도(서브) 앱은 우리 앱이 아니다 → false.**
    카드/혜택/발급 등 앱과 무관한 일반 이야기도 false.
- reason: 그 판단을 내린 근거를 한국어 한 문장으로 (예: "혜택 축소에 대한 불만")

결과는 classify_posts 도구로 제출한다. results 배열 길이는 입력 게시글 수와 정확히 같아야 하고,
각 원소의 idx 는 입력의 idx 와 같아야 한다.

게시글 목록:
{items_json}
"""


CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "relevant": {"type": "boolean"},
                    "type": {"type": "string", "enum": sorted(VALID_TYPES)},
                    "category": {"type": "string", "enum": sorted(VALID_CATEGORIES)},
                    "sentiment": {"type": "string", "enum": sorted(VALID_SENTIMENTS)},
                    "app_related": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["idx", "relevant", "type", "category", "sentiment",
                             "app_related", "reason"],
            },
        }
    },
    "required": ["results"],
}


def normalize_results(data, expected_len):
    """도구 입력 {"results": [...]} 을 입력 순서(idx)대로 정규화한다.
    누락·이상값이 있으면 ValueError(호출부에서 해당 배치 폴백)."""
    rows = (data or {}).get("results")
    if not isinstance(rows, list):
        raise ValueError("results 가 배열이 아님")
    by_idx = {}
    for pos, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("원소가 객체가 아님")
        idx = row.get("idx", pos)
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = pos
        by_idx.setdefault(idx, row)
    if any(i not in by_idx for i in range(expected_len)):
        raise ValueError(f"결과 누락: {len(by_idx)}/{expected_len}")

    out = []
    for i in range(expected_len):
        row = by_idx[i]
        typ = row.get("type", "소비자여론")
        if typ not in VALID_TYPES:
            typ = "소비자여론"
        cat = row.get("category", "")
        if cat not in VALID_CATEGORIES:
            cat = ""  # _apply 에서 키워드 폴백으로 채운다(항상 존재 보장).
        sent = row.get("sentiment", "중립")
        if sent not in VALID_SENTIMENTS:
            sent = "중립"
        out.append({
            "relevant": bool(row.get("relevant", True)),
            "type": typ,
            "category": cat,
            "sentiment": sent,
            "app_related": bool(row.get("app_related", False)),
            "reason": str(row.get("reason", "")).strip(),
            "score": _SCORE[sent],
        })
    return out


def _apply(item, result):
    item["relevant"] = result["relevant"]
    item["type"] = result["type"]
    # category 는 모델이 유효값을 주면 그대로, 아니면 키워드 규칙으로 확정(항상 존재).
    cat = result.get("category", "")
    item["category"] = cat if cat in VALID_CATEGORIES else _category_kw(item)
    item["sentiment"] = result["sentiment"]
    item["app_related"] = result["app_related"]
    item["reason"] = result["reason"]
    item["score"] = result["score"]


# 폴백 앱 판별용 키워드 (API 실패 시에도 app_related 필드가 항상 존재하게).
_APP_KEYWORDS = ("앱", "어플", "로그인", "업데이트", "오류", "버그")

# 카테고리 폴백 키워드. 헨리/챗봇을 결제보다 먼저 검사(우선순위).
_CHATBOT_KEYWORDS = ("헨리", "챗봇", "상담봇", "henry", "Henry", "AI상담", "ai상담")
# 결제 = 현대카드로 '실제 지불(결제)하는 행위'와 '결제수단'만. 좁게 잡는다.
# 한도/할부/청구/연회비/발급/적립(M포인트)은 결제행위가 아니므로 '결제'에서 제외(→기타).
# 'M포인트 적립'류가 결제로 새는 것을 막기 위해 포인트/한도/할부/청구/승인·취소 단독어는 뺐다.
_PAY_KEYWORDS = (
    "삼성페이", "애플페이", "앱카드", "네이버페이", "카카오페이", "간편결제",
    "온라인결제", "오프라인결제", "자동결제", "해외결제", "터치결제", "컨택리스",
    "결제오류", "결제실패", "결제거절", "결제안", "승인문자", "결제",
)


def _is_app_related_kw(item):
    text = f"{item.get('title', '')} {item.get('snippet', '')}"
    return any(k in text for k in _APP_KEYWORDS)


def _category_kw(item):
    """키워드 기반 카테고리 확정. 헨리·챗봇·상담봇 → 챗봇,
    실제 결제/결제수단(삼성페이·애플페이·앱카드·간편/온·오프라인 결제·결제오류) → 결제,
    그 외(한도·할부·청구·적립·발급 포함) → 기타."""
    text = f"{item.get('title', '')} {item.get('snippet', '')}"
    if any(k in text for k in _CHATBOT_KEYWORDS):
        return "챗봇"
    if any(k in text for k in _PAY_KEYWORDS):
        return "결제"
    return "기타"


def _fallback_one(item, note=""):
    """키워드 기반 폴백. 유형은 소비자여론으로 두고 relevant=True 유지."""
    label, score = sentiment.classify(f"{item.get('title', '')} {item.get('snippet', '')}")
    item["relevant"] = True
    item["type"] = "소비자여론"
    item["category"] = _category_kw(item)
    item["sentiment"] = label
    item["app_related"] = _is_app_related_kw(item)
    item["reason"] = note or "키워드 분류(폴백)"
    item["score"] = score if score in (-1, 0, 1) else (_SCORE.get(label, 0))


def _classify_chunk(chunk):
    payload = [
        {"idx": i, "title": it.get("title", ""), "snippet": it.get("snippet", "")}
        for i, it in enumerate(chunk)
    ]
    prompt = PROMPT_TEMPLATE.format(
        items_json=json.dumps(payload, ensure_ascii=False, indent=0)
    )
    data = call_claude(SYSTEM_PROMPT, prompt, "classify_posts",
                       "게시글별 분류 결과를 제출한다.", CLASSIFY_SCHEMA)
    return normalize_results(data, len(chunk))


NEWS_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "relevant": {"type": "boolean"},
                    "ko": {"type": "string"},
                },
                "required": ["idx", "relevant", "ko"],
            },
        }
    },
    "required": ["results"],
}

NEWS_SYSTEM = (
    "너는 현대카드(카드사) 결제·고객경험(CX) 실무자를 위한 뉴스 다이제스트 큐레이터다. "
    "결과는 반드시 지정한 도구로 제출한다."
)

NEWS_PROMPT = (
    "각 항목(t=제목, src=언론사)이 '실무자가 업무상 참고할 가치가 있는가'를 판정(relevant)하라.\n"
    "관련(true): 결제 기술/수단, 카드업계·경쟁 카드사 동향, 핀테크, 간편결제, "
    "결제 브랜드(비자·마스터카드·스트라이프·페이팔·아멕스 등) 신규 기술/전략/제휴, "
    "여행 결제, 금융권 대고객 상담·챗봇·AI 상담(콜센터 AI 포함).\n"
    "무관(false): 스포츠·연예·경기장, 단순 할인/문화/공연 이벤트 홍보, 지자체 지원금, "
    "특정 인물 프로필·수상, 지역 사건사고, 결제와 무관한 일반 기업 소식.\n"
    "또한 src(언론사)가 '공신력 없는 출처'면 relevant=false로 하라: 개인 블로그, 무명 "
    "어그리게이터, 시민/국민기자, 정체불명 외국어 잡사이트, 뉴스 재방송/요약 채널 등. "
    "공신력 매체(블룸버그·로이터·주요 경제지, 국내 주요 언론·경제지·전자신문 등)를 선호.\n"
    "또 각 헤드라인을 자연스러운 한국어로 번역(ko)하라(브랜드 고유명사는 통용 표기 유지, "
    "이미 한국어면 그대로).\n"
    "결과는 curate_news 도구로, 입력과 같은 개수·같은 idx 로 제출한다.\n\n"
)


def curate_news(entries):
    """뉴스 헤드라인 관련성 판정 + 한국어 번역(Claude 1콜).

    entries: [{"title", "press", "foreign"}]. 반환: [{"relevant": bool, "ko": str}]
    (입력과 동수·동순). 실패 시 전부 relevant=True·ko=원제목(과도한 탈락 방지)."""
    entries = list(entries or [])
    fallback = [{"relevant": True, "ko": e.get("title", "")} for e in entries]
    if not entries or not config.ANTHROPIC_API_KEY:
        return fallback
    payload = [{"idx": i, "t": e.get("title", ""), "src": e.get("press", ""),
                "foreign": bool(e.get("foreign"))} for i, e in enumerate(entries)]
    try:
        data = call_claude(NEWS_SYSTEM, NEWS_PROMPT + json.dumps(payload, ensure_ascii=False),
                           "curate_news", "뉴스별 관련성·번역 결과를 제출한다.", NEWS_SCHEMA)
        by_idx = {}
        for r in data.get("results") or []:
            if isinstance(r, dict) and isinstance(r.get("idx"), int):
                by_idx.setdefault(r["idx"], r)
        out = []
        for i, e in enumerate(entries):
            r = by_idx.get(i)
            if r is None:
                out.append({"relevant": True, "ko": e.get("title", "")})
                continue
            ko = str(r.get("ko") or "").strip() or e.get("title", "")
            out.append({"relevant": bool(r.get("relevant", True)), "ko": ko})
        return out
    except Exception:
        STATS["fallback"] += 1
        return fallback


def annotate(items, batch_size=BATCH_SIZE):
    """items 를 제자리에서 분류해 relevant/type/sentiment/reason/score 를 채운다.
    폴백된 항목은 it["fallback"]=True 로 표시된다(리포트·대시보드 배지)."""
    if not items:
        return items

    if not config.ANTHROPIC_API_KEY:
        for it in items:
            _fallback_one(it, note="API 키 미설정(폴백)")
            it["fallback"] = True
        STATS["fallback"] += len(items)
        return items

    for i in range(0, len(items), batch_size):
        chunk = items[i : i + batch_size]
        try:
            results = _classify_chunk(chunk)
            for it, res in zip(chunk, results):
                _apply(it, res)
                it["fallback"] = False
        except Exception as e:  # 이 배치만 폴백, 나머지는 계속
            for it in chunk:
                _fallback_one(it, note=f"분류 실패(폴백): {type(e).__name__}")
                it["fallback"] = True
            STATS["fallback"] += len(chunk)

    return items


# ---------------------------------------------------------------------------
# 오늘의 이슈 요약 (소비자여론을 긍/부정/중립 이슈 불릿으로 1회 요약)
# ---------------------------------------------------------------------------

SUMMARY_PROMPT = """다음은 오늘 수집·분류된 '현대카드' 관련 소비자여론 게시글이다.
sentiment(긍정/부정/중립)별로 어떤 이슈들이 있었는지 짧은 한국어 불릿으로 묶어라.

규칙:
- 각 sentiment 당 2~4개 불릿. 비슷한 글은 하나의 이슈로 묶는다.
- 각 불릿은 명사구 위주의 짧은 한 줄(예: "앱 로그인 오류 관련 불만").
- 해당 sentiment 게시글이 없으면 빈 배열.
- 결과는 submit_summary 도구로 제출한다.

게시글 목록(sentiment | 제목 | 사유):
{items_json}
"""

_SUMMARY_SYSTEM = (
    "너는 현대카드 소비자여론을 감정별 주요 이슈로 요약하는 분석가다. "
    "결과는 반드시 지정한 도구로 제출한다."
)


def _fallback_summary(consumer):
    """API 실패 시: 각 sentiment 에서 대표 reason 을 2~3개 그냥 나열."""
    out = {"부정": [], "긍정": [], "중립": []}
    for sent in out:
        seen = []
        for it in consumer:
            if it.get("sentiment") != sent:
                continue
            reason = (it.get("reason") or "").strip()
            if reason and reason not in seen:
                seen.append(reason)
            if len(seen) >= 3:
                break
        out[sent] = seen
    return out


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        s: {"type": "array", "items": {"type": "string"}} for s in ("부정", "긍정", "중립")
    },
    "required": ["부정", "긍정", "중립"],
}


def summarize_issues(consumer):
    """소비자여론 리스트 → {"부정":[...], "긍정":[...], "중립":[...]} 이슈 불릿.

    Claude 를 1회 호출한다. 키가 없거나 호출이 실패하면 대표 reason 나열로 폴백한다.
    """
    if not consumer:
        return {"부정": [], "긍정": [], "중립": []}

    if not config.ANTHROPIC_API_KEY:
        return _fallback_summary(consumer)

    payload = [
        {
            "sentiment": it.get("sentiment", "중립"),
            "title": it.get("title", ""),
            "reason": it.get("reason", ""),
        }
        for it in consumer
    ]
    prompt = SUMMARY_PROMPT.format(
        items_json=json.dumps(payload, ensure_ascii=False, indent=0)
    )
    try:
        data = call_claude(_SUMMARY_SYSTEM, prompt, "submit_summary",
                           "감성별 이슈 불릿을 제출한다.", SUMMARY_SCHEMA, max_tokens=2048)
        out = {"부정": [], "긍정": [], "중립": []}
        for sent in out:
            vals = data.get(sent, [])
            if isinstance(vals, list):
                out[sent] = [s for v in vals if (s := _flatten_bullet(v))]
        return out
    except Exception:
        STATS["fallback"] += 1
        return _fallback_summary(consumer)


def _flatten_bullet(v):
    """요약 불릿이 리스트/딕셔너리로 중첩돼 오면 안쪽 텍스트만 평문으로 뽑는다.
    (모델이 가끔 ["텍스트"] 처럼 감싸 보내 대괄호가 그대로 찍히는 것 방지)"""
    if isinstance(v, list):
        return " ".join(_flatten_bullet(x) for x in v).strip()
    if isinstance(v, dict):
        return " ".join(str(x) for x in v.values()).strip()
    return str(v).strip()
