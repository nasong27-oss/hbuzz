"""사이트별 수집기.

각 수집기는 keyword를 받아 다음 형태의 dict 리스트를 반환한다:
  {"source": "클리앙", "title": ..., "link": ..., "snippet": ..., "date": ...}

수집은 사이트당 검색 요청 1회(키워드당)로 제한한다 — 하루 한두 번 실행이
전제이므로 사람이 검색하는 수준의 트래픽만 발생시킨다.
"""
import asyncio
import datetime
import html
import os
import re
import time

import requests
from bs4 import BeautifulSoup

import config

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

REQUEST_DELAY = 2  # 사이트 간 최소 간격(초)


def _strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _get(url, extra_headers=None, **kwargs):
    time.sleep(REQUEST_DELAY)
    headers = dict(HEADERS, **(extra_headers or {}))
    resp = requests.get(url, headers=headers, timeout=15, **kwargs)
    resp.raise_for_status()
    return resp


def _post(url, data=None, extra_headers=None, **kwargs):
    """일부 사이트(보배드림)는 검색을 POST로만 받는다. 요청 횟수는 GET과 동일하게 1회."""
    time.sleep(REQUEST_DELAY)
    headers = dict(HEADERS, **(extra_headers or {}))
    resp = requests.post(url, data=data, headers=headers, timeout=15, **kwargs)
    resp.raise_for_status()
    return resp


_DATE_RE = re.compile(r"\d{2,4}[.\-/]\s?\d{1,2}[.\-/]\s?\d{1,2}")

# 절대 날짜: "2026.08.12 20:24", "26.08.13", "26. 08. 13", "2026-08-13" 등.
# 시각(20:24)은 구분자가 ':'라 걸리지 않는다. 그룹1=연, 2=월, 3=일.
_ABS_DATE_RE = re.compile(r"(\d{2,4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})")
# 트위터 created_at: "Wed Aug 13 20:24:00 +0000 2026"
_EN_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_TWITTER_RE = re.compile(r"\b([A-Z][a-z]{2})\s+(\d{1,2})\b.*?\b(\d{4})\b")


def parse_post_date(raw):
    """사이트별로 제각각인 날짜 문자열을 datetime.date 로 정규화한다.

    반환:
      * datetime.date — 확실히 파싱된 날짜 (2자리 연도는 20xx 로 보정)
      * None          — 빈 값이거나 확실히 특정 날짜로 못 박는 경우(예: 'N시간 전')

    보수 원칙: 확실히 파싱된 경우에만 날짜를 돌려준다. 애매하면 None 을 줘서
    호출부가 '드롭' 대신 '날짜 미상'으로 남기게 한다.
    상대 표현은 (사용자 확인: "역산해도 된다" — 경과시간을 지금 시각에서 빼서
    '진짜 날짜'를 계산한다):
      * 'N시간 전' / 'N분 전' / 'N초 전' → now - N 을 역산한 실제 날짜.
        (예: 새벽 실행 시 '23시간 전' → 자정을 넘겨 어제가 될 수 있음. 이게 정답.)
      * '방금' → 오늘(D-0).
      * '어제' → D-1, '그저께/그제' → D-2, 'N일 전' → D-N.
      * 'N주 전' → D-7N, 'N개월/N달 전' → D-30N(근사), 'N년 전' → D-365N(근사).
        (주/개월/년은 대부분 오래된 글이라 어제 필터에서 드롭되지만, '날짜 미상'으로
        남기지 않고 확정 날짜를 줘서 조용히 오래된 글로 처리되게 한다.)
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    now = datetime.datetime.now()
    today = now.date()

    # --- 상대 표현 ---
    # 시/분/초 전은 실제 경과시간을 역산해 '진짜 날짜'를 낸다(자정을 넘겼으면 어제).
    m = re.search(r"(\d+)\s*시간\s*전", s)
    if m:
        return (now - datetime.timedelta(hours=int(m.group(1)))).date()
    m = re.search(r"(\d+)\s*분\s*전", s)
    if m:
        return (now - datetime.timedelta(minutes=int(m.group(1)))).date()
    m = re.search(r"(\d+)\s*초\s*전", s)
    if m:
        return (now - datetime.timedelta(seconds=int(m.group(1)))).date()
    if "방금" in s:
        return today
    if "그저께" in s or "그제" in s:
        return today - datetime.timedelta(days=2)
    if "어제" in s:
        return today - datetime.timedelta(days=1)
    m = re.search(r"(\d+)\s*일\s*전", s)
    if m:
        return today - datetime.timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s*주\s*전", s)
    if m:
        return today - datetime.timedelta(weeks=int(m.group(1)))
    m = re.search(r"(\d+)\s*(?:개월|달)\s*전", s)
    if m:
        return today - datetime.timedelta(days=30 * int(m.group(1)))
    m = re.search(r"(\d+)\s*년\s*전", s)
    if m:
        return today - datetime.timedelta(days=365 * int(m.group(1)))

    # --- 'N월 M일' (연도 생략, 네이버 통합검색 형식) ---
    # 연도가 없으면 올해로 보되, 그 날짜가 오늘보다 미래면 작년으로 보정한다.
    # (예: 오늘 8/14 인데 '10월 9일' → 작년 10월 9일)
    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", s)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            cand = datetime.date(today.year, mo, d)
        except ValueError:
            return None
        if cand > today:
            try:
                cand = datetime.date(today.year - 1, mo, d)
            except ValueError:
                return None
        return cand

    # --- 절대 날짜 (숫자형) ---
    m = _ABS_DATE_RE.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:  # 2자리 연도 보정: 26 -> 2026
            y += 2000
        try:
            return datetime.date(y, mo, d)
        except ValueError:
            return None

    # --- 트위터 영문 날짜 ---
    m = _TWITTER_RE.search(s)
    if m and m.group(1) in _EN_MONTHS:
        try:
            return datetime.date(int(m.group(3)), _EN_MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            return None

    return None


# ---------------------------------------------------------------- 네이버 카페
def naver_cafe(keyword):
    """네이버 공식 검색 API — cafearticle. 날짜 필드는 제공되지 않는다."""
    resp = requests.get(
        "https://openapi.naver.com/v1/search/cafearticle.json",
        params={"query": keyword, "display": 30, "sort": "date"},
        headers={
            "X-Naver-Client-Id": config.NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
        },
        timeout=15,
    )
    resp.raise_for_status()
    items = []
    for it in resp.json().get("items", []):
        items.append({
            "source": f"네이버카페({_strip_tags(it.get('cafename', ''))})",
            "title": _strip_tags(it.get("title")),
            "link": it.get("link", ""),
            "snippet": _strip_tags(it.get("description")),
            "date": "",
        })
    return items


# 네이버 카페 웹 검색(대체 경로). 공식 검색 API는 검색 scope 등록 불가로 폐기됐고,
# 대신 로그인/JS 없이 서버렌더링 HTML을 돌려주는 '카페' 탭(ssc=tab.cafe.all)을 쓴다.
# 최신순(st=date, nso=so:dd,p:all)으로 정렬해 '어제/오늘' 글이 맨 앞에 오게 한다
# (관련도순은 몇 주 전 글이 섞여 대부분 어제-필터에서 드롭됐다).
# 네이버가 CSS class 를 난독화·수시 변경할 수 있어 href 패턴(cafe.naver.com/<카페>/<글번호>)
# 으로 결과 카드를 잡는다. 다만 카페 탭 카드의 제목/요약 링크는 안정적으로 class 에
# 'title'/'dsc' 가 들어 있어(있으면) 우선 활용하고, 없으면 구조 기반으로 폴백한다.
_CAFE_HREF_RE = re.compile(r"cafe\.naver\.com/[^/]+/\d+")

# 결과 카드 안의 날짜 토큰. 절대형(2026.08.13. / 8월 13일)과 상대형을 모두 커버.
# 상대형에 '주/개월/달/년 전'을 포함해야 오래된 글이 '날짜 미상'으로 새지 않는다
# (카페 탭 최신순에도 인기글로 '1주 전' 같은 오래된 글이 섞여 나온다).
_CAFE_DATE_RE = re.compile(
    r"\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.?"      # 2026.08.13. (마침표/공백 옵션)
    r"|\d{1,2}\s*월\s*\d{1,2}\s*일"          # 8월 13일
    r"|\d+\s*일\s*전"                        # 3일 전
    r"|\d+\s*주\s*전"                        # 1주 전
    r"|\d+\s*(?:개월|달)\s*전"               # 2개월 전 / 3달 전
    r"|\d+\s*년\s*전"                        # 1년 전
    r"|어제|그제|그저께"                      # 어제/그제
    r"|\d+\s*시간\s*전"                      # 5시간 전
    r"|\d+\s*분\s*전"                        # 40분 전
    r"|\d+\s*초\s*전"                        # 30초 전
    r"|방금"                                 # 방금 전
)

# 거래글(카드깡/기프티콘 판매·양도, 중고 판매 등) 필터. 이런 글은 여론이 아니라
# 장터 노이즈라 제외한다. 제목에는 전체 목록을, 스니펫에는 오탐이 적은 '강한' 신호만
# 적용한다(스니펫엔 '구매'처럼 정상 여론 글에도 흔한 말이 섞여 과필터 위험이 큼 —
# 예: "차량구매하면서 실적 잡혔어요" 같은 정상 글이 '구매'로 잘못 걸리는 것 방지).
# 제목 신호: 팝니다/판매/삽니다/양도/직거래/시세/최저가/급처/카드깡/현금화/
#            예약중/나눔/상품권/ㅍㅍ, 그리고 앞뒤가 한글이 아닐 때의 '팜'/'삼'.
# 주의: 맨 '구매'와 맨 '거래'는 정상 여론 글에도 흔해(예: "폴드8 구매했어요",
#       "거래정지 당했어요") 과필터 위험이 커서 제외한다. 진짜 장터글은 대부분
#       판매/양도/팝니다/시세 등 더 강한 신호를 함께 달고 있어 재현율 손실은 없다.
#       '직거래'는 장터 전용어라 유지한다.
_CAFE_TRADE_TITLE_RE = re.compile(
    r"팝니다|팝니당|판매합니다|판매완료|판매중|판매|삽니다|양도합니다|양도|직거래"
    r"|시세|최저가|급처|카드깡|현금화|예약중|나눔|상품권|ㅍㅍ"
    r"|(?<![가-힣])팜(?![가-힣])|(?<![가-힣])삼(?![가-힣])"
)
# 스니펫 신호(강한 것만): 팝니다/판매/양도/직거래/시세/최저가/급처/카드깡/현금화/예약중/상품권.
_CAFE_TRADE_SNIPPET_RE = re.compile(
    r"팝니다|판매합니다|판매완료|판매중|판매|양도합니다|양도|직거래"
    r"|시세|최저가|급처|카드깡|현금화|예약중|상품권|ㅍㅍ"
)

# 여러 검색어 변형으로 각각 최신순 카페 검색 후 합친다(앱 관련 여론까지 포괄하려
# '앱' 변형 포함). base URL 기준으로 중복 제거. 나중에 조정하기 쉽게 상수로 뺀다.
_CAFE_QUERY_VARIANTS = ["현대카드", "현카", "현대카드앱", "현카앱", "현대카드 챗봇", "현대카드 헨리",
                        "현카 헨리", "헨리", "아사모 현대카드", "아사모 현카"]  # 아사모(아이폰 카페) 필수 커버
# 앱 리뷰 수집기와 동일한 '실행당 1회' 런마커. collect_all 이 전역 KEYWORDS 마다
# naver_cafe_web 을 호출하므로, 첫 호출이 위 변형 전체를 한 번에 돌려 합쳐 반환하고
# 이후 호출은 []를 반환해 중복 요청/중복 결과를 막는다.
_CAFE_FETCHED = set()


def _cafe_has_cls(a, needle):
    """앵커의 class 목록 중 하나라도 needle 을 포함하면 True (class 리스트 기준 매칭)."""
    return needle in " ".join(a.get("class") or []).lower()


def _cafe_card(anchor, own_link):
    """앵커에서 위로 올라가며 '이 글만' 담긴 가장 바깥 조상(결과 카드)을 찾는다.

    다른 카페 글 링크가 등장하기 직전까지가 이 글의 카드다. 옆 글의 제목/날짜/요약을
    잘못 집는 것을 이 경계로 막는다. class 난독화에 견디도록 href 만으로 경계를 잡는다."""
    node = anchor
    card = anchor
    for _ in range(12):
        node = node.parent
        if node is None:
            break
        others = {
            a["href"].split("?", 1)[0]
            for a in node.find_all("a", href=True)
            if _CAFE_HREF_RE.search(a["href"])
        } - {own_link}
        if others:
            break
        card = node
    return card


def _cafe_title(card, own_link):
    """결과 카드 안에서 제목을 뽑는다. class 에 'title' 이 있는 링크를 우선 쓰고,
    없으면 이 글로 향하는 카페 링크 중 '요약(dsc)'이 아닌 가장 짧은 텍스트를 제목으로
    본다(요약은 제목을 접두로 반복해 더 길기 때문에 '가장 짧은 것'이 대체로 제목)."""
    for a in card.find_all("a", href=True):
        if _cafe_has_cls(a, "title"):
            t = a.get_text(" ", strip=True).replace("새 창 열림", "").strip()
            if t:
                return t
    cands = []
    for a in card.find_all("a", href=True):
        if not _CAFE_HREF_RE.search(a["href"]):
            continue
        if a["href"].split("?", 1)[0] != own_link:
            continue
        if _cafe_has_cls(a, "dsc"):
            continue
        t = a.get_text(" ", strip=True).replace("새 창 열림", "").strip()
        if t:
            cands.append(t)
    return min(cands, key=len) if cands else ""


def _cafe_snippet(card):
    """카드 안 본문 미리보기(설명) 텍스트를 뽑아 ~300자로 자른다. 없으면 ''.
    카페 탭은 로그인 없이도 각 카드에 1~3문장 미리보기를 노출한다(class 'dsc')."""
    for a in card.find_all("a", href=True):
        if _cafe_has_cls(a, "dsc"):
            return a.get_text(" ", strip=True)[:300]
    return ""


def _cafe_name(card, cafeid):
    """결과 카드 안에서 이 글이 속한 카페의 한글 표시명을 뽑는다(예: '레몬테라스').

    각 카드에는 카페 루트로 향하는 링크(cafe.naver.com/<카페id>, 글번호 없음)가 있고
    그 링크 텍스트가 카페 표시명이다. class 난독화에 견디도록 href 로만 고르고, 옆 글/
    다른 카페 이름 오집을 막기 위해 카페id 가 이 글과 같은 루트 링크만 본다(썸네일/로고
    링크는 텍스트가 비어 건너뛴다). 공백을 정리하고 너무 긴 이름(부가설명 포함)은 20자로 자른다."""
    root_re = re.compile(r"cafe\.naver\.com/" + re.escape(cafeid) + r"(?:[/?#]|$)")
    for a in card.find_all("a", href=True):
        href = a["href"]
        if not root_re.search(href):
            continue
        if re.search(r"cafe\.naver\.com/[^/?#]+/\d", href):
            continue  # 글 링크(번호 있음)는 카페명이 아니므로 제외 — 루트 링크만
        name = " ".join(a.get_text(" ", strip=True).split())
        if name:
            return name[:20].strip()
    return ""


def _cafe_post_date(card):
    """결과 카드 텍스트에서 첫 날짜 토큰을 찾아 원본 문자열 그대로 돌려준다.
    카페 탭 카드에는 카페명 옆에 날짜(예: '10분 전', '1주 전', '2026.08.13.')가 항상
    들어 있어 카드 텍스트 스캔만으로 거의 100% 잡힌다. 못 찾으면 '' (→ '날짜 미상')."""
    m = _CAFE_DATE_RE.search(card.get_text(" ", strip=True))
    return m.group(0) if m else ""


# 네이버 카페탭 페이지네이션(start 파라미터):
#   * start 를 붙여야 네이버가 '관련도 섞임'(옛날 글 혼입) → '깔끔한 날짜내림차순'으로
#     응답한다(2026-08 관측). start 없이는 1페이지에 2015년 글까지 섞여 나왔다.
#   * 날짜순이므로 어떤 페이지의 글이 전부 윈도우(CAFE_WINDOW_DAYS) 밖이면 이후 페이지는
#     모두 더 과거 → 그 변형의 페이지 넘김을 멈춘다(불필요한 요청 차단).
CAFE_WINDOW_DAYS = 14        # main.MAX_WINDOW 과 맞춤(챗봇 14일까지 포괄). 이 밖이면 중단
NAVER_PAGE_STEP = 30         # start 증가폭(페이지당 결과 ~30건)
NAVER_MAX_PAGES = 10         # 변형당 최대 페이지(고volume 변형 폭주 방지 안전캡)


def _naver_cafe_web_fetch(keyword, start=1):
    """검색어 하나로 카페 탭(최신순) 1페이지 조회 → 결과 카드 리스트(dict)로 파싱.
    start(1,31,61,...)로 페이지를 넘긴다. start 파라미터가 있어야 날짜순 응답이 온다."""
    resp = _get(
        "https://search.naver.com/search.naver",
        params={
            "ssc": "tab.cafe.all",
            "query": keyword,
            "st": "date",            # 최신순 정렬
            "nso": "so:dd,p:all",    # 정렬=날짜내림차순, 기간=전체
            "sm": "mtb_opt",
            "start": str(start),     # 페이지 오프셋(+ 날짜순 정상화 트리거)
        },
    )
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _CAFE_HREF_RE.search(href):
            continue
        link = href.split("?", 1)[0]
        if link in seen:
            continue
        seen.add(link)
        card = _cafe_card(a, link)
        title = _cafe_title(card, link)
        if not title:
            continue
        # 카페별 한글 표시명을 라벨에 넣는다(예: '네이버카페(맘스홀릭베이비)').
        # 이름을 못 찾으면 기존처럼 plain '네이버카페' 로 폴백.
        cafeid_m = re.search(r"cafe\.naver\.com/([^/?#]+)/\d+", link)
        cafeid = cafeid_m.group(1) if cafeid_m else ""
        name = _cafe_name(card, cafeid)
        source = f"네이버카페({name})" if name else "네이버카페"
        # 아사모(아이폰사용자모임) = 네이버 카페 id 'appleiphone'. 아이폰/애플페이 관련
        # 카페 글이 눈에 띄도록 이 카페만 잘 알려진 이름 '아사모'로 고정 표시한다
        # (다른 표시명이 파싱돼도 아사모로 덮어쓴다).
        if cafeid == "appleiphone" or re.search(r"cafe\.naver\.com/appleiphone(?:/|$)", link):
            source = "네이버카페(아사모)"
        out.append({
            "source": source,
            "title": title,
            "link": link,
            "snippet": _cafe_snippet(card),
            # 원본 날짜 토큰을 그대로 저장한다 — parse_post_date 가 절대/상대형을
            # 모두 정규화(시/분 전은 역산)하므로 저장·필터 라운드트립이 맞다.
            "date": _cafe_post_date(card),
        })
    return out


def naver_cafe_web(keyword):
    """네이버 '카페' 탭(최신순)을 여러 검색어 변형으로 저빈도 조회해 합친다.

    - 실행당 1회(런마커 _CAFE_FETCHED): 첫 호출이 _CAFE_QUERY_VARIANTS 전체를 돌려
      base URL 기준으로 합쳐 반환하고, 같은 실행의 이후 호출은 []를 반환한다. 그래야
      변형×중복 키워드로 요청이 불어나지 않고 변형 집합을 정확히 통제할 수 있다.
    - 최신순(st=date)이라 어제/오늘 글이 맨 앞에 온다. 로그인/JS 불필요(서버렌더 HTML).
    - 거래글(판매/양도/시세 등)은 여론이 아니라 장터 노이즈라 제외한다.
    - 날짜/제목/요약은 각 결과 카드 범위 안에서만 뽑아 옆 글 값 오집을 막는다.
    - 페이지네이션: 변형마다 start(1,31,61,...)로 날짜순 페이지를 넘기되, 페이지 전체가
      윈도우(CAFE_WINDOW_DAYS) 밖으로 넘어가면 즉시 멈춘다. 고volume 변형만 깊게 가고
      저volume 변형은 1페이지에서 끝나 요청이 과하게 늘지 않는다.
    저빈도(하루 1~2회) 사용 전제 — robots.txt 상 비허용이나 사용자 승인됨.
    """
    if "cafe" in _CAFE_FETCHED:
        return []
    _CAFE_FETCHED.add("cafe")

    today = datetime.date.today()
    items = []
    seen = set()
    for kw in _CAFE_QUERY_VARIANTS:
        for page in range(NAVER_MAX_PAGES):
            start = 1 + page * NAVER_PAGE_STEP
            try:
                rows = _naver_cafe_web_fetch(kw, start=start)
            except Exception as e:
                # 변형 하나가 실패해도 나머지는 계속 — 전체 수집을 막지 않는다.
                print(f"[네이버카페] '{kw}' start={start} 검색 실패(무시하고 진행): {type(e).__name__}: {e}")
                break  # 이 변형은 중단, 다음 변형으로
            if not rows:
                break  # 결과 끝
            # 중단 판정은 '이 페이지의 원본 글'이 윈도우 안에 하나라도 있는지로 한다
            # (중복 제거된 신규 건수로 판정하면, 변형끼리 겹칠 때 조기 종료될 수 있음).
            raw_in_window = 0
            for it in rows:
                d = parse_post_date(it.get("date", ""))
                dd = d.date() if isinstance(d, datetime.datetime) else d
                if dd and (today - dd).days <= CAFE_WINDOW_DAYS:
                    raw_in_window += 1
                if it["link"] in seen:
                    continue
                seen.add(it["link"])
                # 거래글 제외: 제목은 전체 목록, 스니펫은 강한 신호만.
                if _CAFE_TRADE_TITLE_RE.search(it["title"]) or _CAFE_TRADE_SNIPPET_RE.search(it["snippet"]):
                    continue
                items.append(it)
            if raw_in_window == 0:
                break  # 이 페이지가 전부 윈도우 밖 → 이후 페이지는 더 과거라 중단
    return items


# ------------------------------------------------------------------- 클리앙
# 검색 결과에 섞여 나오는 순수 뉴스 게시판(사용자 글이 아님)은 제외한다.
# 링크 경로가 /service/board/<code>/ 형태라 board code 로 걸러낸다.
_CLIEN_NEWS_BOARDS = {"news", "cnews", "pcnews"}


def clien(keyword):
    resp = _get(
        "https://www.clien.net/service/search",
        params={"q": keyword, "sort": "recency", "boardCd": "", "isBoard": "false"},
    )
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    for row in soup.select(".list_item"):
        a = row.select_one("a.subject_fixed") or row.select_one(".list_subject a")
        if not a:
            continue
        link = a.get("href", "")
        # 뉴스 게시판 글 제외 (예: /service/board/news/..., /service/board/cnews/...)
        m = re.search(r"/service/board/([^/]+)/", link)
        if m and m.group(1) in _CLIEN_NEWS_BOARDS:
            continue
        if link.startswith("/"):
            link = "https://www.clien.net" + link
        date_el = row.select_one(".timestamp")
        # 검색 결과 행의 본문 미리보기(div.preview > span)를 요약으로 채운다.
        snippet_el = row.select_one(".preview span") or row.select_one(".preview")
        snippet = snippet_el.get_text(" ", strip=True)[:150] if snippet_el else ""
        items.append({
            "source": "클리앙",
            "title": a.get_text(" ", strip=True),
            "link": link,
            "snippet": snippet,
            "date": date_el.get_text(strip=True) if date_el else "",
        })
    return items


# -------------------------------------------------------------------- 뽐뿌
def ppomppu(keyword):
    """search_bbs.php — EUC-KR. 통합검색 기본 페이지는 장터/비즈파트너 광고가 섞여
    나오므로 bbs_cate=2(커뮤니티)로 좁힌다. 실제 DOM은
    div.results_board > div.conts > div.content 구조다."""
    # 사이트가 EUC-KR이라 키워드도 EUC-KR로 퍼센트 인코딩해서 붙인다.
    kw = requests.utils.quote(keyword.encode("euc-kr"))
    resp = _get(
        "https://www.ppomppu.co.kr/search_bbs.php"
        "?bbs_cate=2&search_type=sub_memo&order_type=date&page_size=30&keyword=" + kw
    )
    soup = BeautifulSoup(resp.content.decode("euc-kr", "replace"), "html.parser")
    items = []
    for row in soup.select("div.conts"):
        classes = row.get("class") or []
        if "biz" in classes:  # 비즈파트너(카드업체) 광고 블록
            continue
        content = row.select_one("div.content") or row
        a = content.select_one("span.title a")
        if not a:
            continue
        for junk in a.select("font.comment-cnt"):  # 제목 뒤 댓글수 제거
            junk.decompose()
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        link = a.get("href", "")
        if link.startswith("//"):
            link = "https:" + link
        elif link.startswith("/"):
            link = "https://www.ppomppu.co.kr" + link
        snippet_el = content.select_one("p:not(.desc)")
        desc = content.select_one("p.desc")
        date = ""
        if desc:
            for span in desc.find_all("span"):
                txt = span.get_text(strip=True)
                if _DATE_RE.fullmatch(txt):
                    date = txt
                    break
        items.append({
            "source": "뽐뿌",
            "title": title,
            "link": link,
            "snippet": snippet_el.get_text(" ", strip=True)[:150] if snippet_el else "",
            "date": date,
        })
    return items


# ----------------------------------------------------------------- 보배드림
def bobaedream(keyword):
    """통합검색은 GET을 무시하고(홈으로 JS 리다이렉트) POST + Referer 를 요구한다.
    Referer 가 없으면 본문 63바이트짜리 location.href 스크립트만 돌아온다."""
    resp = _post(
        "https://www.bobaedream.co.kr/search",
        data={
            "colle": "community",
            "searchField": "ALL",
            "page": "1",
            "sort": "DATE",
            "startDate": "",
            "keyword": keyword,
        },
        extra_headers={"Referer": "https://www.bobaedream.co.kr/"},
    )
    soup = BeautifulSoup(resp.content.decode("utf-8", "replace"), "html.parser")
    items = []
    for row in soup.select("div.search_Community ul li"):
        a = row.select_one("dt a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        link = a.get("href", "")
        if link.startswith("/"):
            link = "https://www.bobaedream.co.kr" + link
        snippet_el = row.select_one("dd.progress")
        date = ""
        path = row.select_one("dd.path")
        if path:
            for span in path.find_all("span"):
                txt = span.get_text(" ", strip=True)
                if _DATE_RE.fullmatch(txt):
                    date = txt
                    break
        items.append({
            "source": "보배드림",
            "title": title,
            "link": link,
            "snippet": snippet_el.get_text(" ", strip=True)[:150] if snippet_el else "",
            "date": date,
        })
    return items


# -------------------------------------------------------------------- 더쿠
def theqoo(keyword):
    """비로그인 수집 불가 — 현재 빈 리스트를 반환한다.

    2026-08 확인 내용:
      * 통합검색(act=IS)은 HTTP 403 + 본문에
        "통합 검색을 사용할 수 없습니다. 통합검색 설정에서 대상 모듈을 선택하세요."
        → Rhymix 통합검색(IS) 모듈이 사이트 차원에서 꺼져 있어 파라미터로 우회 불가.
      * 게시판 단위 검색(/square, /hot 에 search_target/search_keyword)은 200 이지만
        키워드가 무시된 기본 목록(공지 + 최신글)이 그대로 돌아온다. 비로그인 페이지
        HTML에는 '검색' 문자열조차 없어 게스트에게 검색 UI 자체가 노출되지 않는다.
    로그인 세션(쿠키)을 붙이기 전에는 복구 방법이 없어 빈 리스트로 둔다.
    """
    return []


# ---------------------------------------------------------------- 디시인사이드
DC_MAX_PAGES = 5   # 통합검색 최신순 페이지 상한(윈도우 밖으로 넘어가면 조기 종료)


def _dcinside_fetch(keyword, page):
    """통합검색 한 페이지(최신순) 조회 → 결과 카드 리스트(dict). /post/p/<page>/q/<kw>.
    이 경로는 기본이 날짜내림차순이라 페이지가 커질수록 과거 글이다."""
    resp = _get(
        "https://search.dcinside.com/post/p/{}/q/{}".format(page, requests.utils.quote(keyword))
    )
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for row in soup.select("ul.sch_result_list li"):
        a = row.select_one("a.tit_txt") or row.select_one("a")
        if not a:
            continue
        snippet_el = row.select_one("p.link_dsc_txt, .dsc_txt")
        date_el = row.select_one(".date_time")
        link = a.get("href", "")
        # 갤러리 표기: 우선 결과 행에 노출되는 한국어 갤러리명(a.sub_txt)을,
        # 없으면 URL의 id= 슬러그를 사용한다. 어떤 경우든 최소한 슬러그는 남긴다.
        name_el = row.select_one("a.sub_txt")
        gallery = name_el.get_text(" ", strip=True) if name_el else ""
        # 갤러리명 자체의 괄호(예: '아도(Ado)', 'QWER(큐떱이알)')는 표기 괄호와 겹쳐
        # '디시인사이드(아도(Ado)' 처럼 깨지므로 첫 '(' 이후를 떼어낸다.
        gallery = re.sub(r"\s*\(.*$", "", gallery).strip()
        if gallery:
            # 너무 길면 접미사('마이너 갤러리'/'갤러리')를 떼고 16자로 자른다.
            if len(gallery) > 16:
                gallery = re.sub(r"\s*마이너\s*갤러리$", "", gallery)
                gallery = re.sub(r"\s*갤러리$", "", gallery)
                gallery = gallery[:16].strip()
        else:
            m = re.search(r"[?&]id=([^&#]+)", link)
            gallery = m.group(1) if m else ""
        source = "디시인사이드({})".format(gallery) if gallery else "디시인사이드"
        out.append({
            "source": source,
            "title": a.get_text(" ", strip=True),
            "link": link,
            "snippet": snippet_el.get_text(" ", strip=True)[:150] if snippet_el else "",
            "date": date_el.get_text(strip=True) if date_el else "",
        })
    return out


def dcinside(keyword):
    """통합검색을 최신순 페이지네이션으로 조회한다(저빈도, 하루 1~2회).
    날짜내림차순이라 어떤 페이지가 전부 윈도우(CAFE_WINDOW_DAYS) 밖이면 이후는 더 과거 → 중단."""
    today = datetime.date.today()
    items, seen = [], set()
    for page in range(1, DC_MAX_PAGES + 1):
        try:
            rows = _dcinside_fetch(keyword, page)
        except Exception as e:
            print(f"[디시인사이드] '{keyword}' p{page} 실패(무시하고 진행): {type(e).__name__}: {e}")
            break
        if not rows:
            break
        raw_in_window = 0
        for it in rows:
            d = parse_post_date(it.get("date", ""))
            dd = d.date() if isinstance(d, datetime.datetime) else d
            if dd and (today - dd).days <= CAFE_WINDOW_DAYS:
                raw_in_window += 1
            if it["link"] in seen:
                continue
            seen.add(it["link"])
            items.append(it)
        if raw_in_window == 0:
            break
    return items


# 디시 특정 갤러리 '직접검색' — 통합검색이 놓치는 핵심 갤러리를 갤러리 내부에서 검색한다.
# (네이버 '아사모'를 따로 보듯, 카드 여론의 핵심인 신용카드/아이폰 갤을 직접 본다.)
# (gid, 표시 라벨, 마이너 갤러리 여부)
_DC_GALLERIES = [
    ("creditcard", "디시인사이드(신용카드갤)", False),
    ("iphone", "디시인사이드(아이폰갤)", False),
]
_DC_GALL_QUERIES = ["현대카드", "현카"]
_DC_GALL_MAX_PAGES = 5
_DC_GALL_FETCHED = set()
_DC_FULL_DT_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def _dc_gallery_fetch(gid, keyword, label, minor=False, page=1):
    """특정 갤러리 내부 검색 한 페이지 → 결과 리스트(dict). 광고/공지 행(글번호 비숫자)은 제외."""
    base = "https://gall.dcinside.com/" + ("mgallery/" if minor else "") + "board/lists/"
    resp = _get(base, params={
        "id": gid,
        "s_type": "search_subject_memo",   # 제목+본문 검색
        "s_keyword": keyword,
        "page": str(page),
    })
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for row in soup.select("tr.ub-content"):
        num = row.select_one("td.gall_num")
        # 광고/공지/설문 행 제외: 글번호가 숫자인 실제 글만 취한다.
        if not (num and num.get_text(strip=True).isdigit()):
            continue
        a = row.select_one("td.gall_tit a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        dcell = row.select_one("td.gall_date")
        raw = (dcell.get("title") or dcell.get_text(strip=True)) if dcell else ""
        date = raw[:10] if _DC_FULL_DT_RE.search(raw) else raw
        link = a.get("href", "")
        if link.startswith("/"):
            link = "https://gall.dcinside.com" + link
        out.append({"source": label, "title": title, "link": link, "snippet": "", "date": date})
    return out


def dcinside_gallery(keyword):
    """디시 핵심 갤러리(신용카드·아이폰)를 '갤러리 내부 검색'으로 직접 수집한다.

    통합검색이 특정 갤러리 글(예: '현대카드 mode 출시')을 놓치는 걸 보완한다. 카드 여론의
    핵심인 신용카드 갤(id=creditcard)과 애플페이·현대카드 논의가 잦은 아이폰 갤을 본다.
    각 갤러리 목록은 최신순이라 윈도우(CAFE_WINDOW_DAYS) 밖으로 넘어가면 페이지 넘김을 멈춘다.
    실행당 1회(런마커). 갤러리별 라벨로 리포트에 '디시인사이드(신용카드갤)'처럼 구분 표기된다."""
    if "done" in _DC_GALL_FETCHED:
        return []
    _DC_GALL_FETCHED.add("done")
    today = datetime.date.today()
    items, seen = [], set()
    for gid, label, minor in _DC_GALLERIES:
        for kw in _DC_GALL_QUERIES:
            for page in range(1, _DC_GALL_MAX_PAGES + 1):
                try:
                    rows = _dc_gallery_fetch(gid, kw, label, minor, page)
                except Exception as e:
                    print(f"[디시갤러리] {label} '{kw}' p{page} 실패(무시하고 진행): {type(e).__name__}: {e}")
                    break
                if not rows:
                    break
                raw_in_window = 0
                for it in rows:
                    d = parse_post_date(it.get("date", ""))
                    dd = d.date() if isinstance(d, datetime.datetime) else d
                    if dd and (today - dd).days <= CAFE_WINDOW_DAYS:
                        raw_in_window += 1
                    if it["link"] in seen:
                        continue
                    seen.add(it["link"])
                    items.append(it)
                if raw_in_window == 0:
                    break
    return items


# ------------------------------------------------------------------ 와이고수
def ygosu(keyword):
    """사이트가 Tailwind 기반으로 리뉴얼되어 예전 셀렉터가 전부 무효.
    현재 결과 컨테이너는 ol#board_search_result > li > article 이다."""
    resp = _get(
        "https://ygosu.com/all_search/",
        params={"type": "board", "keyword": keyword},
    )
    soup = BeautifulSoup(resp.content.decode("utf-8", "replace"), "html.parser")
    items = []
    for row in soup.select("ol#board_search_result > li"):
        a = row.select_one("article a[href*='/board/']")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        link = a.get("href", "")
        if link.startswith("/"):
            link = "https://ygosu.com" + link
        time_el = row.select_one("time")
        date = ""
        if time_el:
            date = (time_el.get("datetime") or "")[:10] or time_el.get_text(strip=True)
        snippet_el = row.select_one("article > p")
        items.append({
            "source": "와이고수",
            "title": title,
            "link": link,
            "snippet": snippet_el.get_text(" ", strip=True)[:150] if snippet_el else "",
            "date": date,
        })
    return items


# ------------------------------------------------------------------- X (트위터)
# twikit(순수 파이썬, 로그인 계정 사용) 기반 *보조* 수집기.
#
# 철칙: 절대 데일리 실행을 막지 않는다(베스트에포트). 로그인이든 검색이든 어떤
#       이유로든 실패하면 짧은 사유를 로그로 남기고 []를 반환한다 — 커뮤니티
#       수집 파이프라인은 그대로 진행된다. collect_all 도 수집기별 try/except 로
#       감싸지만, 여기서도 내부적으로 한 번 더 방어한다.
#
# 밴 위험 최소화: 최초 1회만 비밀번호 로그인하고 세션 쿠키를 data/x_cookies.json 에
#       저장한다. 이후 실행은 쿠키를 로드해 재로그인을 건너뛴다(매 실행 로그인은
#       밴 트리거 1순위). 쿠키가 없거나 만료된 경우에만 새 비밀번호 로그인을 한다.
X_COOKIES_PATH = os.path.join(config.DATA_DIR, "x_cookies.json")
X_TWEET_COUNT = 20  # 키워드당 최근 트윗 대략 20건 (저빈도, 키워드당 검색 1회)


def _x_tweet_to_item(tweet):
    """twikit Tweet 객체를 표준 dict 스키마로 매핑한다."""
    text = (tweet.full_text or tweet.text or "").strip()
    one_line = " ".join(text.split())  # 개행/연속공백 제거 → 한 줄
    screen_name = getattr(getattr(tweet, "user", None), "screen_name", "") or "i"
    link = f"https://x.com/{screen_name}/status/{tweet.id}"
    return {
        "source": "X",
        "title": one_line[:80],
        "link": link,
        "snippet": one_line[:200],
        "date": getattr(tweet, "created_at", "") or "",
    }


async def _x_login_and_search(keyword):
    """twikit 로그인(쿠키 우선) 후 키워드 1회 검색 → 표준 dict 리스트."""
    from twikit import Client  # 지연 임포트: 미설치/임포트 실패가 다른 수집기에 영향 없게

    client = Client("en-US")

    async def run_search():
        result = await client.search_tweet(keyword, product="Latest", count=X_TWEET_COUNT)
        return [_x_tweet_to_item(t) for t in list(result)[:X_TWEET_COUNT]]

    # 1) 캐시된 쿠키가 있으면 로드하고, 실제 검색으로 세션 유효성까지 함께 확인한다.
    if os.path.exists(X_COOKIES_PATH):
        try:
            client.load_cookies(X_COOKIES_PATH)
            return await run_search()
        except Exception as e:
            # 쿠키 만료/무효 추정 → 아래에서 새 로그인 시도. (밴 위험이라 최소화)
            print(f"[X] 캐시 쿠키 재사용 실패({type(e).__name__}) → 재로그인 시도")

    # 2) 쿠키 없음/만료 → 비밀번호로 새 로그인 후 쿠키 저장.
    await client.login(
        auth_info_1=config.X_ACCOUNT,
        auth_info_2=(config.X_USERNAME or None),
        password=config.X_PASSWORD,
    )
    client.save_cookies(X_COOKIES_PATH)
    return await run_search()


def x_search(keyword):
    """동기 수집기 진입점. 실패는 절대 전파하지 않고 []를 반환한다(베스트에포트)."""
    if not config.X_ACCOUNT or not config.X_PASSWORD:
        print("[X] 자격증명(X_ACCOUNT/X_PASSWORD) 미설정 → X 수집 건너뜀")
        return []
    time.sleep(REQUEST_DELAY)  # REQUEST_DELAY 정신 유지 — 타이트 루프 금지
    try:
        return asyncio.run(_x_login_and_search(keyword))
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        hint = ""
        if not config.X_USERNAME:
            # @handle 미확보 상태의 실패 → 챌린지로 인해 @username 이 필요할 수 있다.
            hint = " | X 로그인에 @username 필요(추정): .env 의 X_USERNAME 에 @handle 설정 후 재시도"
        print(f"[X] 수집 실패(무시하고 진행): {msg}{hint}")
        return []


# ------------------------------------------------------------------ 에펨코리아
# 검색 결과는 fm_best_widget 위젯 목록(li.li)으로 렌더된다. 제목 앵커의 href 는
# /best/{srl} 또는 /{srl} 형태의 숫자 문서 링크이고, 추천수 앵커(a.pc_voted_count)는
# 같은 href 를 갖지만 텍스트가 '추천 N'이라 제외한다. 날짜는 span.regdate.
_FM_DOC_HREF_RE = re.compile(r"^/(?:[a-z]+/)?\d+$")


def fmkorea(keyword):
    """에펨코리아 통합검색(제목+내용). Cloudflare 챌린지가 뜨면 소프트 실패(빈 리스트).

    fmkorea 는 Cloudflare 뒤에 있어 봇 판정 시 'Just a moment' 챌린지 페이지를 준다.
    그 경우 크래시 대신 짧은 로그만 남기고 []를 반환한다(공격적 재시도 금지)."""
    url = (
        "https://www.fmkorea.com/search.php?mid=home&where=document"
        "&search_target=title_content&search_keyword=" + requests.utils.quote(keyword)
    )
    try:
        resp = _get(url)
    except Exception as e:
        print(f"[에펨코리아] 요청 실패(무시하고 진행): {type(e).__name__}: {e}")
        return []
    body = resp.text
    if "Just a moment" in body or "challenge-platform" in body or "cf-challenge" in body:
        print("[에펨코리아] Cloudflare 챌린지 감지 → 소프트 실패([] 반환)")
        return []
    soup = BeautifulSoup(body, "html.parser")
    items = []
    seen = set()
    for li in soup.select("div.fm_best_widget li.li"):
        try:
            a = None
            for cand in li.find_all("a", href=True):
                classes = cand.get("class") or []
                if any("pc_voted_count" in c for c in classes):
                    continue  # 추천수 앵커 제외
                if _FM_DOC_HREF_RE.match(cand["href"]) and cand.get_text(strip=True):
                    a = cand
                    break
            if a is None:
                continue
            title = a.get_text(" ", strip=True)
            title = re.sub(r"\s*\[\d+\]\s*$", "", title).strip()  # 말미 댓글수 [776] 제거
            if not title:
                continue
            link = "https://www.fmkorea.com" + a["href"]
            if link in seen:
                continue
            seen.add(link)
            date_el = li.select_one("span.regdate")
            items.append({
                "source": "에펨코리아",
                "title": title,
                "link": link,
                "snippet": "",
                "date": date_el.get_text(strip=True) if date_el else "",
            })
        except Exception:
            continue  # 한 행이 깨져도 수집기 전체는 계속
    return items


# -------------------------------------------------------------------- MLBPARK
def mlbpark(keyword):
    """MLBPARK 불펜 검색(sct=제목+내용). 결과는 table.tbl_type01 의 tr 이고,
    제목은 div.tit > a.txt(절대 링크), 날짜는 같은 tr 의 span.date(예 2026-08-12)."""
    resp = _get(
        "https://mlbpark.donga.com/mp/b.php?m=search&b=bullpen&select=sct&query="
        + requests.utils.quote(keyword)
    )
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    seen = set()
    for a in soup.select("div.tit > a.txt"):
        try:
            title = a.get_text(" ", strip=True)
            if not title:
                continue
            link = a.get("href", "")
            if link.startswith("/"):
                link = "https://mlbpark.donga.com" + link
            if not link or link in seen:
                continue
            seen.add(link)
            row = a.find_parent("tr")
            date = ""
            if row:
                date_el = row.select_one("span.date")
                if date_el:
                    date = date_el.get_text(strip=True)
            items.append({
                "source": "MLBPARK",
                "title": title,
                "link": link,
                "snippet": "",
                "date": date,
            })
        except Exception:
            continue
    return items


# --------------------------------------------------------------------- 루리웹
# 루리웹 통합검색 결과 링크: https://bbs.ruliweb.com/{cat}/board/{boardId}/read/{postId}
_RULIWEB_READ_RE = re.compile(r"^https://bbs\.ruliweb\.com/[^/]+/board/\d+/read/\d+")


def ruliweb(keyword):
    """루리웹 통합검색. 결과 카드는 li.search_result_item, 제목 앵커는
    a.title.text_over, 날짜는 같은 카드의 .time(예 2026.08.12 / 어제)."""
    resp = _get("https://bbs.ruliweb.com/search?q=" + requests.utils.quote(keyword))
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    seen = set()
    for a in soup.select("a.title.text_over"):
        try:
            href = a.get("href", "")
            if not _RULIWEB_READ_RE.match(href):
                continue
            link = href.split("?", 1)[0]
            if link in seen:
                continue
            title = a.get_text(" ", strip=True)
            if not title:
                continue
            seen.add(link)
            row = a.find_parent("li") or a.parent
            date = ""
            if row:
                date_el = row.select_one(".time")
                if date_el:
                    date = date_el.get_text(strip=True)
            items.append({
                "source": "루리웹",
                "title": title,
                "link": link,
                "snippet": "",
                "date": date,
            })
        except Exception:
            continue
    return items


# ------------------------------------------------------------------- 네이트판
def nate_pann(keyword):
    """네이트판 검색. 결과는 li > div.tit > h2 > a.subject(상대 링크 /talk/{id}),
    날짜는 div.info > span.date(예 25.08.11 14:23)."""
    resp = _get("https://pann.nate.com/search/talk?q=" + requests.utils.quote(keyword))
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    seen = set()
    for a in soup.select("a.subject"):
        try:
            href = a.get("href", "")
            if not re.match(r"^/talk/\d+", href):
                continue
            link = "https://pann.nate.com" + href.split("?", 1)[0]
            if link in seen:
                continue
            title = a.get("title") or a.get_text(" ", strip=True)
            title = title.strip()
            if not title:
                continue
            seen.add(link)
            li = a.find_parent("li")
            date = ""
            if li:
                date_el = li.select_one("span.date")
                if date_el:
                    date = date_el.get_text(strip=True)
            items.append({
                "source": "네이트판",
                "title": title,
                "link": link,
                "snippet": "",
                "date": date,
            })
        except Exception:
            continue
    return items


# ----------------------------------------------------------------- 오늘의유머
def todayhumor(keyword):
    """오늘의유머 시사게시판 제목 검색. 결과는 tr, 제목 앵커는 board/view.php?...no={no}
    (썸네일용 no_tag=1 링크는 텍스트가 번호뿐이라 제외), 날짜는 tr 안 '25/05/27 23:54' 형식."""
    resp = _get(
        "https://www.todayhumor.co.kr/board/list.php"
        "?table=sisa&kind=search&keyfield=subject&keyword=" + requests.utils.quote(keyword)
    )
    soup = BeautifulSoup(resp.content.decode("utf-8", "replace"), "html.parser")
    items = []
    seen = set()
    for a in soup.find_all("a", href=True):
        try:
            href = a["href"]
            if "view.php" not in href:
                continue
            m = re.search(r"[?&]no=(\d+)", href)
            if not m:
                continue
            no = m.group(1)
            title = a.get_text(" ", strip=True)
            if not title or title == no:  # 썸네일(no_tag=1) 링크는 텍스트가 번호뿐 → 제외
                continue
            if no in seen:
                continue
            seen.add(no)
            if href.startswith("/"):
                link = "https://www.todayhumor.co.kr" + href
            elif href.startswith("http"):
                link = href
            else:
                link = "https://www.todayhumor.co.kr/board/" + href.lstrip("./")
            row = a.find_parent("tr")
            date = ""
            if row:
                dm = re.search(r"\d{2,4}/\d{1,2}/\d{1,2}", row.get_text(" ", strip=True))
                if dm:
                    date = dm.group(0)
            items.append({
                "source": "오늘의유머",
                "title": title,
                "link": link,
                "snippet": "",
                "date": date,
            })
        except Exception:
            continue
    return items


# ---------------------------------------------------------------------- 82cook
def cook82(keyword):
    """82cook 자유게시판 제목 검색. 결과는 li, 제목 앵커는 read.php?num={num},
    날짜는 같은 li 안 'YYYY-MM-DD' 형식."""
    resp = _get(
        "https://www.82cook.com/entiz/search.php?search_type=title&keyword="
        + requests.utils.quote(keyword)
    )
    soup = BeautifulSoup(resp.content.decode("utf-8", "replace"), "html.parser")
    items = []
    seen = set()
    for a in soup.find_all("a", href=True):
        try:
            href = a["href"]
            if "read.php" not in href:
                continue
            m = re.search(r"num=(\d+)", href)
            if not m:
                continue
            num = m.group(1)
            if num in seen:
                continue
            title = a.get_text(" ", strip=True)
            title = re.sub(r"\s+\d+$", "", title).strip()  # 말미 댓글수 제거
            if not title:
                continue
            seen.add(num)
            if href.startswith("/"):
                link = "https://www.82cook.com" + href
            elif href.startswith("http"):
                link = href
            else:
                link = "https://www.82cook.com/entiz/" + href.lstrip("./")
            row = a.find_parent("li") or a.find_parent("tr")
            date = ""
            if row:
                dm = re.search(r"\d{4}-\d{1,2}-\d{1,2}", row.get_text(" ", strip=True))
                if dm:
                    date = dm.group(0)
            items.append({
                "source": "82cook",
                "title": title,
                "link": link,
                "snippet": "",
                "date": date,
            })
        except Exception:
            continue
    return items


# ------------------------------------------------ 앱스토어 리뷰 (현대카드 앱)
# 현대카드 모바일 앱(현대카드 M / appcard) 스토어 리뷰 수집기.
#
# 철칙(X 수집기와 동일한 베스트에포트): 네트워크/라이브러리/레이트리밋 등 어떤
#   실패도 파이프라인을 막지 않는다 — 짧은 로그를 남기고 []를 반환한다.
#
# 키워드 무관: 리뷰는 이미 '그 앱'에 대한 글이라 검색 키워드를 쓰지 않는다.
#   collect_all 은 키워드마다(현대카드/현카) 각 수집기를 호출하므로, 같은 리뷰가
#   두 번 반환되지 않도록 실행당 소스별 1회만 수집하고 이후엔 []를 반환한다
#   (아래 _APP_FETCHED 런마커). 링크에는 리뷰 id 프래그먼트를 붙여 리뷰마다
#   고유하게 만든다 — main.py 의 링크 기준 중복제거가 동일 링크를 하나로 접어버려
#   같은 스토어 페이지를 공유하는 리뷰들이 1건으로 뭉개지는 것을 막기 위함이다
#   (프래그먼트 #review-<id> 는 같은 스토어 페이지로 그대로 열린다).
APP_REVIEW_SOURCES = {"구글플레이", "앱스토어"}
_APP_FETCHED = set()  # 실행당 소스별 1회 수집 런마커 (프로세스 수명 동안 유지)

GPLAY_PACKAGE = "com.hyundaicard.appcard"
GPLAY_LINK = "https://play.google.com/store/apps/details?id=com.hyundaicard.appcard"
GPLAY_REVIEW_COUNT = 40  # 최근 ~40건이면 어제치 커버에 충분(빠르게, 안 걸리게 우선)

APPLE_APP_ID = 702653088
APPLE_LINK = "https://apps.apple.com/kr/app/id702653088"
# 앱스토어 RSS는 페이지당 ~50건이라 40건이면 사실상 1페이지(요청 1회)로 끝난다.
APPLE_REVIEW_COUNT = 40
APPLE_TIMEOUT = 30  # 앱스토어 수집 전체 소요 상한(초). 아침 콜드스타트 여유로 20→30.
_KST = datetime.timezone(datetime.timedelta(hours=9))


def google_play_reviews(keyword):
    """구글플레이 '현대카드 M' 앱 리뷰(최신순). 키워드 무시, 실행당 1회, 베스트에포트.

    google-play-scraper 의 reviews() 는 (list[dict], continuation_token) 을 준다.
    각 dict: content(본문), score(별점 1~5), at(작성일시, naive datetime, KST 기준).
    """
    if "구글플레이" in _APP_FETCHED:
        return []  # 두 번째 키워드부터는 재수집하지 않는다(동일 리뷰 중복 방지)
    _APP_FETCHED.add("구글플레이")
    try:
        from google_play_scraper import reviews, Sort  # 지연 임포트(미설치 방어)
        result, _token = reviews(
            GPLAY_PACKAGE,
            lang="ko",
            country="kr",
            sort=Sort.NEWEST,
            count=GPLAY_REVIEW_COUNT,
        )
    except Exception as e:
        print(f"[구글플레이] 리뷰 수집 실패(무시하고 진행): {type(e).__name__}: {e}")
        return []
    items = []
    for r in result:
        try:
            content = (r.get("content") or "").strip()
            if not content:
                continue
            score = r.get("score")
            star = f"★{score}" if score else "★?"
            at = r.get("at")
            date = at.strftime("%Y-%m-%d") if at else ""
            first = " ".join(content.split())[:40]  # 제목용 앞 40자(한 줄)
            rid = r.get("reviewId") or ""
            items.append({
                "source": "구글플레이",
                "title": f"{star} {first}".strip(),
                # 리뷰별 고유 링크(같은 앱 페이지). 프래그먼트로 중복제거를 통과시킨다.
                "link": f"{GPLAY_LINK}#review-{rid}" if rid else GPLAY_LINK,
                "snippet": content[:300],
                "date": date,
            })
        except Exception:
            continue  # 리뷰 한 건이 깨져도 수집기 전체는 계속
    return items


def apple_appstore_reviews(keyword):
    """앱스토어 '현대카드' 앱 리뷰(최신순). 키워드 무시, 실행당 1회, 베스트에포트.

    app-store-web-scraper 는 amp-api 를 쓰며(구 iTunes RSS 폐기), AppStoreEntry(app_id,
    country).reviews(limit=...) 가 AppReview 를 순회하는 이터레이터를 준다(페이지네이션).
    AppReview 필드: id, date(tz-aware datetime, 애플 서버 타임존), user_name, title,
    content, rating(1~5), app_version. date 는 KST 로 변환해 날짜를 뽑는다.
    """
    if "앱스토어" in _APP_FETCHED:
        return []
    _APP_FETCHED.add("앱스토어")

    def _fetch():
        # 애플 amp-api 가 429/503 로 쓰로틀하면 라이브러리 세션이 최대 5회
        # 지수 백오프(최대 ~135초/요청)로 오래 매달린다. 아래 스레드+타임아웃으로
        # 전체 소요를 APPLE_TIMEOUT 초로 강제 상한한다.
        from app_store_web_scraper import AppStoreEntry  # 지연 임포트
        entry = AppStoreEntry(app_id=APPLE_APP_ID, country="kr")
        out, count = [], 0
        for rv in entry.reviews(limit=APPLE_REVIEW_COUNT):
            count += 1
            if count > APPLE_REVIEW_COUNT:
                break
            try:
                content = (rv.content or "").strip()
                title = (rv.title or "").strip()
                star = f"★{rv.rating}" if rv.rating else "★?"
                d = rv.date
                if d is not None and d.tzinfo is not None:
                    d = d.astimezone(_KST)  # KST 변환 후 날짜 산출(어제 필터 정합)
                date = d.strftime("%Y-%m-%d") if d else ""
                out.append({
                    "source": "앱스토어",
                    "title": f"{star} {title}".strip() if title else f"{star} (제목 없음)",
                    "link": f"{APPLE_LINK}#review-{rv.id}",
                    "snippet": content[:300],
                    "date": date,
                })
            except Exception:
                continue
        return out

    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_fetch).result(timeout=APPLE_TIMEOUT)
    except concurrent.futures.TimeoutError:
        print(f"[앱스토어] {APPLE_TIMEOUT}s 초과(애플 쓰로틀링 추정) — 이번 회차 건너뜀")
        return []
    except Exception as e:
        print(f"[앱스토어] 리뷰 수집 실패(무시하고 진행): {type(e).__name__}: {e}")
        return []


# ------------------------------------------------------- 금융·카드업계 뉴스
# 소비자 여론과 별개로, 카드업계 + 결제/핀테크 '테크' 최근 뉴스를 별도 섹션으로 붙인다.
# 무료·안정적인 Google 뉴스 RSS(깔끔한 XML: 제목·언론사·발행일·링크)를 쓴다.
# (네이버 뉴스는 sds-comps 난독화 클래스로 바뀌어 파싱이 취약함)
# Google 뉴스 지역 에디션(관심 지역만: 국내·미국·유럽·동남아·일본). 인도 등은 제외.
_NEWS_EDITIONS = {
    "ko":  {"hl": "ko",    "gl": "KR", "ceid": "KR:ko"},
    "us":  {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "eu":  {"hl": "en-GB", "gl": "GB", "ceid": "GB:en"},
    "sea": {"hl": "en-SG", "gl": "SG", "ceid": "SG:en"},
    "jp":  {"hl": "ja",    "gl": "JP", "ceid": "JP:ja"},
}
# 뉴스 초점(사용자 지정): ① 해외 브랜드사(비자·마스터·스트라이프 등) 신규 결제기술
# ② 타 카드사 동향 ③ 여행 기반 결제기술 ④ 실리콘밸리(우버·호텔스닷컴 등) 대고객 챗봇.
# 해외는 국내 영향도 있는 글로벌 브랜드/기술 중심 + 관심 지역 에디션. (쿼리, 에디션).
# 발송 전날(어제) 하루치만 넣고, 국내 아닌 에디션 기사는 최종 목록에 든 것만 KO로 번역.
NEWS_FEEDS = [
    ("현대카드 결제 OR 현대카드 앱 OR 현대카드 챗봇 OR 현대카드 헨리 OR 현대카드 애플페이", "ko"),
    ("(신한카드 OR 삼성카드 OR KB국민카드 OR 롯데카드 OR 우리카드 OR 하나카드) (결제 OR 페이 OR 핀테크 OR AI OR 앱 OR 제휴 OR 기술)", "ko"),
    ("비자 OR 마스터카드 OR 스트라이프 OR 페이팔 OR 아멕스 결제", "ko"),
    ("여행 결제 OR 트래블카드 OR 트래블월렛 OR 해외여행 결제 OR 여행 핀테크", "ko"),
    # 미국: 결제 브랜드/기술 + 실리콘밸리 대고객 챗봇
    ("Visa OR Mastercard OR Stripe OR PayPal OR Adyen payment technology", "us"),
    ("Uber OR Airbnb OR DoorDash OR Booking.com customer service AI OR support chatbot", "us"),
    # 유럽: 핀테크/결제
    ("Revolut OR Wise OR Klarna OR Adyen OR Curve payment fintech", "eu"),
    # 동남아: 결제/핀테크
    ("Grab OR GrabPay OR GoTo OR Sea Limited OR Southeast Asia digital payment", "sea"),
    # 일본: 캐시리스/결제
    ("キャッシュレス OR 決済 OR フィンテック OR 楽天ペイ OR PayPay", "jp"),
]
# 제외: ① 지자체(지역화폐·재난/민생지원금 등) ② 관심 밖 지역(인도 등). 영문·현지어 지명 포함.
_NEWS_EXCLUDE_RE = re.compile(
    r"지역화폐|지역사랑상품권|재난지원금|민생회복|민생지원|상생소비|상생지원금|지자체|지방자치|"
    r"India|Delhi|Mumbai|Bengaluru|Bangalore|Kolkata|Chennai|Hyderabad|Rupee|"
    r"인도|델리|뭄바이|벵갈루루|루피|파키스탄|Pakistan|Nepal|네팔|Bangladesh|방글라데시|"
    # 결제 브랜드명(PayPay/Visa 등)이 스포츠·행정문서에 오탐되는 것 제외(원제목이 일본어일 수 있어 병기)
    r"야구|baseball|Hawks|호크스|프로야구|ホークス|野球|サッカー|"
    r"Solomon|솔로몬|전자\s*비자|e-?[Vv]isa",
    re.I)
# 해외(비-ko 에디션)는 공신력 있는 언론사만 통과 — 어그리게이터·라디오 재방송·블로그 배제.
# (블룸버그·로이터·FT·CNBC·닛케이·핀테크 전문매체 등 '블룸버그/로이터 급'만)
_NEWS_CREDIBLE_FOREIGN = re.compile(
    r"Bloomberg|Reuters|Financial Times|\bFT\b|Wall Street Journal|WSJ|CNBC|CNN|"
    r"Forbes|Fortune|The Economist|Business Insider|Associated Press|AP News|"
    r"The Guardian|\bBBC\b|Axios|Quartz|"
    r"TechCrunch|The Verge|Wired|Engadget|Ars Technica|"
    r"Finextra|PYMNTS|The Paypers|American Banker|Payments Dive|Banking Dive|"
    r"Fintech Futures|Sifted|Tearsheet|The Block|CoinDesk|Cointelegraph|"
    r"Nikkei Asia|日経|日本経済新聞|ITmedia|Impress Watch|東洋経済|ダイヤモンド|"
    r"NHK|朝日新聞|読売新聞|時事通信|共同通信|"
    r"Yahoo Finance|MarketWatch|Barron|"
    r"Straits Times|Tech in Asia|\be27\b|KrASIA|DealStreetAsia",
    re.I)
# 명백한 저품질 출처는 언론사명으로 결정적 차단(Claude 폴백 때도 안 새게). 애매한 건 Claude가 판정.
_NEWS_JUNK_SOURCE_RE = re.compile(
    r"아이러브피시방|아이러브PC|블로그|blogspot|tistory|wordpress|medium\.com|naver\.me|"
    r"v\.daum\.net|Histoire|vietnam\.vn|BigGo|시민기자|국민기자", re.I)
# 한 회사(엔티티)가 리포트를 도배하지 않게 회사당 최대 노출 수를 제한한다.
# (예: 신한카드 문화혜택 할인 PR이 여러 언론사로 재탕돼 6건씩 들어오는 것 방지)
NEWS_PER_ENTITY = 2
# (별칭들, 대표명): 국문·영문 표기를 하나로 통합해야 캡이 언어 넘어 먹는다.
# (예: '비자'와 'Visa'를 따로 세면 폴백 번역 실패 시 Visa 기사 4개가 새어나감)
_NEWS_ENTITY_CANON = [
    (["신한카드"], "신한카드"), (["삼성카드"], "삼성카드"),
    (["KB국민카드", "국민카드"], "KB국민카드"), (["롯데카드"], "롯데카드"),
    (["우리카드"], "우리카드"), (["하나카드"], "하나카드"), (["현대카드"], "현대카드"),
    (["BC카드", "비씨카드"], "BC카드"), (["토스"], "토스"),
    (["카카오페이"], "카카오페이"), (["네이버페이"], "네이버페이"),
    (["비자", "Visa"], "비자"), (["마스터카드", "Mastercard"], "마스터카드"),
    (["스트라이프", "Stripe"], "스트라이프"), (["페이팔", "PayPal"], "페이팔"),
    (["페이페이", "PayPay"], "페이페이"), (["아멕스", "American Express", "Amex"], "아멕스"),
    (["레볼루트", "Revolut"], "레볼루트"), (["와이즈", "Wise"], "와이즈"),
    (["우버", "Uber"], "우버"), (["에어비앤비", "Airbnb"], "에어비앤비"),
]


def _news_entity(title):
    """제목에서 대표 회사(엔티티)명을 캐노니컬로 반환(회사당 노출 제한용). 없으면 None.
    국문·영문 별칭을 하나로 묶어 언어가 달라도 같은 회사로 센다."""
    low = title.lower()
    for aliases, canon in _NEWS_ENTITY_CANON:
        for a in aliases:
            if a in title or a.lower() in low:
                return canon
    return None


NEWS_MAX = 12
_NEWS_CURATE_POOL = 24   # 관련성 판정(Claude)에 넘길 상위 후보 수(최신순). 입력 크기 제한.
# 챗봇/상담(AI) 뉴스는 사용자 핵심 관심사 — 어제치 풀에서 최소 이만큼은 우선 노출 시도.
NEWS_AI_MIN = 4
NEWS_FOREIGN_MIN = 3   # 해외(영문) 금융권 AI·챗봇 기사도 최소 이만큼 보장 노출
_NEWS_AI_RE = re.compile(
    r"생성형|인공지능|챗봇|\bAI\b|LLM|챗GPT|GPT|제미나이|클로드|코파일럿|"
    r"generative|chatbot|assistant|copilot", re.I)
# 같은 사건 판별 시 무시할 흔한 뉴스 동사/부가어(핵심 고유명사만 남기려고).
_NEWS_STOP = {"출시", "공개", "선보여", "발표", "예정", "혜택", "서비스", "상품",
              "전용", "신규", "오픈", "이벤트", "관련", "위해", "통해"}


def _news_sig(title):
    """제목에서 '같은 사건' 판별용 시그니처(핵심 단어 집합). BC/비씨 등 표기 정규화·숫자 제거."""
    t = title.lower().replace("bc", "비씨").replace("kb", "국민")
    t = re.sub(r"[0-9%]+", "", t)
    return {w for w in re.findall(r"[가-힣a-z]+", t) if len(w) >= 2 and w not in _NEWS_STOP}


def _news_key(title):
    """날짜를 넘나드는(cross-day) 중복 판별용 안정 키 — 같은 기사 재노출 방지.
    소문자화 후 한글·영문·숫자만 남긴 정규화 문자열(공백·기호 제거)."""
    return re.sub(r"[^0-9a-z가-힣]", "", title.lower())


def _resolve_news_url(google_url):
    """Google 뉴스 RSS 리다이렉트 링크(평균 210자)를 실제 언론사 원문 URL로 디코딩한다.
    기사 페이지의 서명(sg/ts)을 뽑아 batchexecute 로 해석. 실패하면 원래 구글 링크 반환."""
    try:
        import json
        m = re.search(r"/articles/([^?]+)", google_url)
        if not m:
            return google_url
        art = m.group(1)
        r = _get("https://news.google.com/rss/articles/" + art)
        sig = re.search(r'data-n-a-sg="([^"]+)"', r.text)
        ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
        if not (sig and ts):
            return google_url
        inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
                 None, None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0,
                 None, 0], art, int(ts.group(1)), sig.group(1)]
        payload = [[["Fbv4je", json.dumps(inner), None, "generic"]]]
        rr = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            data="f.req=" + requests.utils.quote(json.dumps(payload)),
            headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8"},
            timeout=15)
        # 응답에서 원문 URL 추출. URL 은 garturlres 뒤에 이중 이스케이프로 온다:
        #   garturlres\",\"https://...idxno\\u003d84682\"   (\\u003d = '=')
        # 이스케이프된 종료 따옴표(\") 전까지 잡은 뒤, \\u003d 등 이중 이스케이프를 '=' 로 복원.
        m2 = re.search(r'garturlres\\",\\"(https?://.*?)\\"', rr.text)
        if m2:
            url = m2.group(1)
            url = url.replace("\\\\u003d", "=").replace("\\u003d", "=")
            url = url.replace("\\\\u0026", "&").replace("\\u0026", "&")
            url = re.sub(r"\\+u[0-9a-fA-F]{4}", "", url)  # 잔여 이스케이프 안전 제거
            if url.startswith("http") and "news.google" not in url:
                return url
        return google_url
    except Exception:
        return google_url


def _fetch_news_feed(q, edition):
    """Google 뉴스 RSS 한 쿼리 조회 → item dict 리스트. edition: _NEWS_EDITIONS 키
    ('ko'=국내, 그 외 us/eu/sea/jp=해외 지역). 발송 전날(어제, KST) 하루치만 남긴다."""
    import urllib.parse
    from email.utils import parsedate_to_datetime
    kst = datetime.timezone(datetime.timedelta(hours=9))
    yesterday = datetime.datetime.now(kst).date() - datetime.timedelta(days=1)
    ed = _NEWS_EDITIONS.get(edition, _NEWS_EDITIONS["ko"])
    params = dict(q=q, **ed)
    out = []
    try:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
        soup = BeautifulSoup(_get(url).content, "xml")
    except Exception as e:
        print(f"[뉴스] '{q}'({lang}) 수집 실패(무시하고 진행): {type(e).__name__}: {e}")
        return out
    for it in soup.find_all("item"):
        title = (it.title.get_text() if it.title else "").strip()
        press = (it.source.get_text() if it.source else "").strip()
        link = (it.link.get_text() if it.link else "").strip()
        pub = it.pubDate.get_text() if it.pubDate else ""
        if not title:
            continue
        try:
            dt = parsedate_to_datetime(pub).astimezone(kst)
        except Exception:
            continue
        if dt.date() != yesterday:   # 발송 전날(어제) 하루치만
            continue
        if _NEWS_EXCLUDE_RE.search(title):   # 지자체(지역화폐·재난지원금 등) 제외
            continue
        if _NEWS_JUNK_SOURCE_RE.search(press or ""):
            continue   # 명백한 저품질 출처(블로그·시민기자·잡사이트)는 결정적 차단
        if edition != "ko" and not _NEWS_CREDIBLE_FOREIGN.search(press or ""):
            continue   # 해외는 공신력 있는 언론사만(어그리게이터·라디오 재방송·블로그 배제)
        if press and title.endswith(press):   # 제목 끝 ' - 언론사' 제거
            title = re.sub(r"\s*-\s*[^-]+$", "", title)
        out.append({"title": title, "press": press, "link": link,
                    "date": dt.strftime("%Y-%m-%d %H:%M"), "foreign": edition != "ko", "_dt": dt})
    return out


def finance_news(seen=None):
    """결제·챗봇/대고객상담 중심 최근 뉴스(국내+해외)를 모아 최신순 상위 NEWS_MAX건 반환.

    - 국내(한국어) + 해외(영어) Google 뉴스 RSS. 해외 기사는 최종 목록에 든 것만 KO로 번역.
    - seen(이전에 내보낸 기사 key 집합)에 있는 기사는 제외 → 날짜 넘어가며 겹치지 않게.
    - 같은 사건(여러 언론사) 중복은 제목 유사도(Jaccard≥0.35)로 제거.
    - 챗봇/상담(AI) 뉴스 최소 NEWS_AI_MIN건, 해외 최소 NEWS_FOREIGN_MIN건 노출 보장.
    - 반환 각 항목에 안정 key 를 실어 호출측이 seen 을 갱신할 수 있게 한다.
    실패해도 []를 반환해 데일리 리포트를 막지 않는다(베스트에포트)."""
    seen = set(seen or [])
    cand = []
    for q, edition in NEWS_FEEDS:
        cand += _fetch_news_feed(q, edition)
    # cross-day 중복 제거: 이전에 내보낸 기사(key)는 뺀다(같은 날 중복 실행 등 안전장치).
    cand = [c for c in cand if _news_key(c["title"]) not in seen]
    cand.sort(key=lambda x: x["_dt"], reverse=True)
    deduped = []
    for it in cand:
        sig = _news_sig(it["title"])
        if any(sig and k["_sig"] and len(sig & k["_sig"]) / len(sig | k["_sig"]) >= 0.30
               for k in deduped):
            continue
        it["_sig"] = sig
        deduped.append(it)

    # 관련성 큐레이션 + 번역(Claude 1콜): 상위 후보만 판정해 입력 크기를 제한한다.
    # 현대카드 실무자 관점에서 무관한 것(스포츠·경기장·이벤트 홍보·지자체·인물 프로필 등) 탈락.
    pool = deduped[:_NEWS_CURATE_POOL]
    import classify
    verdicts = classify.curate_news(
        [{"title": p["title"], "press": p.get("press", ""), "foreign": p.get("foreign", False)}
         for p in pool])
    curated = []
    for p, v in zip(pool, verdicts):
        p["key"] = _news_key(p["title"])   # 번역 전 원제목 기준(안정) — cross-day dedup 용
        if not v.get("relevant"):
            continue
        if v.get("ko"):
            p["title"] = v["ko"]           # 한국어 번역/정규화 제목으로 교체
        curated.append(p)

    # 선정: 회사당 캡 + 해외(NEWS_FOREIGN_MIN)·AI/챗봇(NEWS_AI_MIN) 최소 노출 + 최신순 top MAX.
    def _is_ai(n):
        return bool(_NEWS_AI_RE.search(n["title"]))
    picked, picked_ids, ent_ct = [], set(), {}

    def _take(pool_, k):
        for n in pool_:
            if len(picked) >= NEWS_MAX or k <= 0:
                break
            if id(n) in picked_ids:
                continue
            e = _news_entity(n["title"])
            if e and ent_ct.get(e, 0) >= NEWS_PER_ENTITY:   # 회사당 도배 방지
                continue
            picked_ids.add(id(n)); picked.append(n); k -= 1
            if e:
                ent_ct[e] = ent_ct.get(e, 0) + 1
    _take([n for n in curated if n.get("foreign")], NEWS_FOREIGN_MIN)  # 해외 최소 보장
    _take([n for n in curated if _is_ai(n)], NEWS_AI_MIN)              # AI/챗봇 최소 보장
    _take(curated, NEWS_MAX)                                           # 나머지는 최신순
    picked.sort(key=lambda x: x["_dt"], reverse=True)
    result = picked[:NEWS_MAX]
    for it in result:
        it["date"] = it["_dt"].strftime("%m/%d")   # 전부 어제 날짜(하루치)
        if it.get("foreign"):
            p = it.get("press")
            it["press"] = f"{p} (해외)" if p else "해외"
        # 구글 뉴스 리다이렉트(평균 210자)를 실제 언론사 원문 URL로 디코딩(최종 12건만).
        if it.get("link"):
            it["link"] = _resolve_news_url(it["link"])
        it.pop("_dt", None)
        it.pop("_sig", None)
        it.pop("foreign", None)
    return result


COLLECTORS = {
    # 옛 공식 검색 API(naver_cafe)는 검색 scope 등록 불가로 폐기 — 함수는 남겨두되
    #   COLLECTORS 에는 넣지 않는다. 대신 웹 검색 기반 naver_cafe_web 을 사용한다.
    "네이버카페": naver_cafe_web,
    "클리앙": clien,
    "뽐뿌": ppomppu,
    "보배드림": bobaedream,
    "더쿠": theqoo,
    "디시인사이드": dcinside,
    # 디시 핵심 갤러리(신용카드·아이폰) 직접검색 — 통합검색 누락 보완(카드 여론 핵심).
    "디시갤러리": dcinside_gallery,
    "와이고수": ygosu,
    # "에펨코리아"(fmkorea)는 사용자 요청(정치색 우려)으로 수집에서 제외한다.
    #   함수 정의는 그대로 남겨둬 나중에 되살릴 수 있게 하고, 여기 등록만 뺀다.
    # "에펨코리아": fmkorea,
    "MLBPARK": mlbpark,
    "루리웹": ruliweb,
    "네이트판": nate_pann,
    "오늘의유머": todayhumor,
    "82cook": cook82,
    "X": x_search,  # 보조 소스(베스트에포트) — 실패해도 []를 반환해 파이프라인을 막지 않는다.
    # 현대카드 앱 스토어 리뷰(베스트에포트, 키워드 무시·실행당 1회). 정의상 앱 관련 소비자여론.
    "구글플레이": google_play_reviews,
    "앱스토어": apple_appstore_reviews,
}


# '현대'와 '카드'가 (거의) 붙어 있어야 진짜 '현대카드' 언급으로 본다.
# 사이트 검색이 두 단어를 따로 매칭해 엉뚱한 글(역사/정치 등)이 섞이는 것을 막는다.
# '현카'(현대카드의 흔한 줄임말)도 허용한다 — KEYWORDS에 '현카'를 추가했으므로
# 이 필터가 '현대카드' 연속형만 요구하면 현카-only 글이 잘못 탈락한다.
# '현대카드앱'/'현카앱'은 자연히 이 패턴을 포함하므로 함께 통과한다.
_RELEVANCE_RE = re.compile(r"(현대\s*카드|현카)")

# 챗봇(헨리) 예외 통과 규칙:
# 카드 커뮤니티의 진짜 헨리-챗봇 글은 "헨리가 응답을 못함"처럼 '현대카드'를 안 쓰고
# '헨리'만 언급할 수 있다. 이런 글을 살리되 무관한 'Henry'(인명·헨리 포드 등) 노이즈는
# 막기 위해, '헨리'가 있으면서 동시에 카드/챗봇 맥락 토큰(챗봇·상담·카드·결제·앱)이
# 하나라도 있을 때만 통과시킨다. 맥락 없는 순수 '헨리'는 여전히 탈락시킨다.
# (Claude의 relevant=false가 이후 잔여 노이즈를 추가로 걸러낸다.)
_HENRY_RE = re.compile(r"헨리")
_HENRY_CONTEXT_RE = re.compile(r"(챗봇|상담|카드|결제|앱)")


def is_relevant(item):
    """제목+요약에 '현대카드'(공백 허용) 또는 '현카'가 등장하면 True.

    단, 앱 스토어 리뷰(구글플레이/앱스토어)는 이미 '현대카드 앱' 자체에 대한 글이라
    본문에 '현대카드'/'현카'가 없어도 관련 글이다 → 소스로 예외 통과시킨다.
    또한 '헨리' + 카드/챗봇 맥락 토큰이 함께 있으면 챗봇(헨리) 글로 보고 통과시킨다.
    """
    if item.get("source") in APP_REVIEW_SOURCES:
        return True
    text = f"{item.get('title', '')} {item.get('snippet', '')}"
    if _RELEVANCE_RE.search(text):
        return True
    # 헨리 챗봇 예외: '헨리' AND (챗봇|상담|카드|결제|앱) 이 함께 있어야만 통과.
    if _HENRY_RE.search(text) and _HENRY_CONTEXT_RE.search(text):
        return True
    return False


# 채무조정·개인회생·신용회복 등 '부채정리' 및 카드영업(모집) 글은 현대카드 소비자
# 여론이 아니라 상담/영업 노이즈다 → 리포트·아카이브에서 아예 제외한다(사용자 요청).
#   * 소스(카페/갤러리명) 기준: 신용회복위원회 공식카페, 개인회생 갤, 회생레시피,
#     채무관리/채무플랜 카페 등.
#   * 본문(제목+요약) 기준: 영사님·설계사·모집(영업), 채무조정·개인회생·워크아웃·
#     신용회복·(방문)추심·대환대출·탕감 등.
# 소스(카페/갤러리명)는 debt 전문 커뮤니티만 매칭 — 이름에 이 토큰이 있으면 부채정리 카페/갤.
_EXCLUDE_SOURCE_RE = re.compile(r"개인\s*회생|회생레시피|신용회복|채무|파산|워크아웃|추심")
# 본문(제목+요약)은 '부분 문자열 오탐'을 피하려 debt 맥락으로 좁힌다.
#   * '회생'은 '사회생활', '파산'은 '지갑파산'(농담성 불만)에 걸리므로 debt 동반어를 요구.
_EXCLUDE_TEXT_RE = re.compile(
    r"채무\s*조정|채무\s*통합|채무\s*관리|개인\s*회생|"
    r"회생\s*(?:신청|접수|인가|개시|절차|위원|폐지|면책|레시피)|"
    r"개인\s*파산|파산\s*(?:신청|면책|선고)|"
    r"신용\s*회복|워크아웃|방문\s*추심|추심|대환|빚\s*탕감|탕감|"
    r"영사\s*님|영사\s*분|영업\s*사원|설계사|모집인"
)


def is_excluded(item):
    """부채정리(채무조정/개인회생/신용회복 등)·카드영업(영사/설계사/모집) 글이면 True.

    소스 라벨(카페/갤러리명)이나 제목+요약에 해당 신호가 있으면 소비자 여론에서 배제한다."""
    if _EXCLUDE_SOURCE_RE.search(item.get("source", "")):
        return True
    text = f"{item.get('title', '')} {item.get('snippet', '')}"
    if _EXCLUDE_TEXT_RE.search(text):
        return True
    return False


def filter_relevant(items):
    """수집 직후·분류 직전에 (1) 오탐(현대/카드 분리 매칭)과 (2) 부채정리·영업 노이즈를 걸러낸다."""
    kept = [it for it in items if is_relevant(it) and not is_excluded(it)]
    dropped = len(items) - len(kept)
    return kept, dropped


# --- 조용한 실패 대비: 0건이면 재시도 후 중복제거해 합침 ---
# 대부분 수집기는 예외를 자체적으로 삼키고 0건을 반환한다(베스트에포트). 그래서
# 일시적 타임아웃·스로틀·연결끊김으로 인한 '조용한 실패'는 하드에러로 안 잡힌다.
# 대책: 어떤 수집기가 0건이면 백오프하며 최대 COLLECT_RETRIES회 다시 시도하고,
# 시도 결과를 URL 기준으로 중복제거해 합친다. 정상 수집(비어있지 않음)된 사이트는
# 재시도하지 않으므로 상대 사이트 부담이 늘지 않는다(봇처럼 보이지 않게).
COLLECT_RETRIES = 3               # 사이트당 최대 시도 횟수
MERGE_ATTEMPTS = 2                # 성공해도 최소 이만큼 시도해 합침(부분 잘림·변동 대비)
_RETRY_BACKOFF = [3, 8]           # 다음 시도 전 대기(초): 2회차 전 3초, 3회차 전 8초
_RETRY_SKIP = {"X"}               # X는 자체 재로그인+상시 실패 → 1회만
# 대형 커뮤니티: 재시도 후에도 0건이면 사실상 수집 실패로 보고 리포트에 표기한다.
# (소규모/저volume 사이트의 0건은 실제로 언급이 없을 수 있어 오탐 방지차 제외)
_EXPECT_DATA = {"네이버카페", "디시인사이드", "디시갤러리", "뽐뿌", "클리앙", "MLBPARK", "루리웹", "더쿠"}
# 페이지네이션 자가감지: 검색 1페이지의 '가장 오래된 글'이 아직 이 일수 안이면,
# 그 사이트는 하루 볼륨이 많아 1페이지가 최근 윈도우를 못 덮었을 수 있다(누락 위험).
# 현재 모든 수집기는 1페이지만 조회한다 — 정상적으론 1페이지가 2일보다 과거까지 닿는다.
_TRUNCATION_WARN_DAYS = 2


def _oldest_age_days(items):
    """items 중 파싱 가능한 가장 오래된 날짜의 '오늘로부터 경과일'. 없으면 None."""
    today = datetime.date.today()
    ages = []
    for it in items:
        d = parse_post_date(it.get("date", ""))
        if d:
            d = d if isinstance(d, datetime.date) and not isinstance(d, datetime.datetime) else (d.date() if isinstance(d, datetime.datetime) else d)
            ages.append((today - d).days)
    return max(ages) if ages else None


def _run_collector(fn, keywords):
    """한 수집기를 모든 키워드에 대해 1회 실행. (items, last_error) 반환.
    개별 키워드 예외는 삼키되(다른 키워드는 진행) 마지막 예외를 함께 돌려준다."""
    items, err = [], None
    for kw in keywords:
        try:
            items.extend(fn(kw))
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    return items, err


def _dedup_by_url(items):
    """URL 기준 중복제거(재시도 결과를 안전하게 합치기 위함). URL 없으면 소스+제목."""
    seen, out = set(), []
    for it in items:
        key = it.get("url") or (it.get("source", ""), it.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


SOURCE_STATS = {}  # 마지막 collect_all 실행의 사이트별 통계


def collect_all(keywords, enabled=None):
    """모든 수집기 실행. 실패한 사이트는 건너뛰고 오류를 함께 반환한다.

    적응형 다중시도 + 합침(중복제거):
      - 성공해도 최소 MERGE_ATTEMPTS회 시도해 합집합을 만든다(부분 잘림·서버 변동 대비).
      - 직전 시도 대비 새 글이 계속 늘면 COLLECT_RETRIES까지 더 시도(변동 큰 사이트).
      - 최소 시도를 채웠고 새로 늘어난 게 없으면(안정) 즉시 종료 → 불필요한 요청 안 함.
      - 끝까지 0건인 대형 커뮤니티(_EXPECT_DATA)나 예외 발생은 errors에 담아 리포트에 표기.
      - X는 자체 재로그인+상시 실패라 1회만 시도한다.
    URL(없으면 소스+제목) 기준으로 시도 간 중복을 제거한다.

    사이트별 건수·소요시간·시도횟수는 SOURCE_STATS 에 남긴다(운영 패널·장애 분석용)."""
    results, errors = [], {}
    SOURCE_STATS.clear()
    for name, fn in COLLECTORS.items():
        if enabled and name not in enabled:
            continue
        t0 = time.time()
        cap = 1 if name in _RETRY_SKIP else COLLECT_RETRIES
        min_tries = 1 if name in _RETRY_SKIP else MERGE_ATTEMPTS
        pool, last_err, used = {}, None, 0
        for attempt in range(cap):
            used = attempt + 1
            items, err = _run_collector(fn, keywords)
            if err:
                last_err = err
            before = len(pool)
            for it in items:
                key = it.get("url") or (it.get("source", ""), it.get("title", ""))
                pool.setdefault(key, it)
            gained = len(pool) - before
            # 최소 시도를 채웠고, 결과가 있고, 이번에 새로 늘어난 게 없으면 안정 → 종료
            if used >= min_tries and pool and gained == 0:
                break
            if attempt < cap - 1:          # 더 시도할 게 남았으면 백오프
                time.sleep(_RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)])
        merged = list(pool.values())
        results.extend(merged)
        SOURCE_STATS[name] = {"count": len(merged), "secs": round(time.time() - t0, 1),
                              "tries": used, "error": (last_err or "")[:200]}
        if merged:
            tag = f" ({used}회 합침)" if used > 1 else ""
            oldest = _oldest_age_days(merged)
            if oldest is not None and oldest < _TRUNCATION_WARN_DAYS:
                # 1페이지 최고참 글이 아직 윈도우 안 → 볼륨 과다로 잘렸을 수 있음
                tag += f" ⚠절단위험(최고참 {oldest}일전 · 페이지확장 검토)"
            print(f"[수집] {name}: {len(merged)}건{tag}")
        else:
            if last_err:                   # 하드에러(예외) → 그대로 보고
                errors[name] = last_err
            elif name in _EXPECT_DATA:     # 대형 커뮤니티 0건 → 수집 실패 추정
                errors[name] = f"0건 — 재시도 {used}회 후에도 수집 실패 추정"
            note = "하드에러" if last_err else ("실패추정" if name in _EXPECT_DATA else "0건(정상일수도)")
            print(f"[수집] {name}: 0건 [{note}]" + (f" · {used}회 시도" if used > 1 else ""))
    return results, errors
