"""HTML + 플레인텍스트 메일 리포트 생성.

수신 환경 제약(중요):
- 아웃룩(MS Word 엔진)에서 렌더링된다: <details>/<summary>·flex·grid·box-shadow
  등을 못 쓴다. 레이아웃은 전부 <table>/<tr>/<td>, CSS 는 전부 인라인.
- 게다가 기업 보안필터가 style 속성을 통째로 제거할 수 있다. 그래서 스타일이
  0% 남아도 읽히도록(graceful degradation) 설계한다:
    * 의미를 색/배경에만 의존하지 않는다 → 각 항목에 [긍정]/[부정]/[중립]
      텍스트 라벨을 실제 글자로 붙인다. 사유는 '- 사유:' 텍스트 접두어.
    * 구조는 시맨틱 태그로: <h2>/<h3>/<table border="1"><th>/<ul>.
      style 이 지워져도 제목·표·목록·셀 경계가 살아 있다.
    * 링크(<a href>)는 유지하되 중요치 않게 둔다(href 는 보통 안 지워짐).
- 진짜 안전판은 함께 보내는 플레인텍스트 본문(build_text)이다.

집계 규칙:
- 긍/부정/중립 집계는 '소비자여론' 항목만 대상.
- 뉴스/채용/가십기타는 항상 보이는 별도 섹션(2차, 회색)으로 분리.
- 소비자여론은 부정 우선 정렬.
"""
import datetime
import html
import urllib.parse


COLORS = {"긍정": "#1a7f37", "부정": "#c93c3c", "중립": "#666666"}

# 표시용(이메일) URL 에서 제거할 검색·정렬·페이지네이션 노이즈 쿼리 키.
# id/no/document_srl 등 '식별자' 키는 유지한다(글을 특정하므로).
_NOISE_QS = {"q", "keyword", "combine", "p", "sort", "boardcd",
             "isboard", "art", "page", "sm", "nso"}


def _short_url(url):
    """이메일 표시용으로 URL 을 짧게 만든다(표시 전용 — 원본 데이터는 안 건드림).

    - 검색/정렬/페이지 노이즈 쿼리 파라미터(_NOISE_QS)를 제거한다.
    - id/no/document_srl 같은 식별자 쿼리는 유지한다(글을 특정).
    - 프래그먼트(#...)는 스토어 리뷰 앵커(#review-)만 남기고 그 외엔 제거한다.
    - urllib.parse 로만 처리하고, 파싱 실패 시 원본을 그대로 반환한다.

    예) clien.net/service/board/park/19246505?combine=true&q=..&sort=recency
        -> clien.net/service/board/park/19246505
    """
    if not url:
        return url
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _NOISE_QS
    ]
    query = urllib.parse.urlencode(kept)
    fragment = parts.fragment if parts.fragment.startswith("review-") else ""
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, query, fragment)
    )
TYPE_LABEL = {"뉴스": "뉴스", "채용": "채용", "가십기타": "가십·기타"}

# 소비자여론 정렬 순서: 부정 → 긍정 → 중립 (그룹 순서). 그룹 내부는 안정 정렬로 유지.
_GROUP_RANK = {"부정": 0, "긍정": 1, "중립": 2}
_SENT_ORDER = ("부정", "긍정", "중립")

FONT = "'Malgun Gothic','Apple SD Gothic Neo',Dotum,sans-serif"


# 리포트 전체를 관통하는 '항목 번호' 러닝 카운터. build_html/build_text 진입 시
# 0 으로 리셋하고, 각 항목 렌더 직전에 _next_no() 로 1씩 증가시킨다. 섹션이 바뀌어도
# 리셋하지 않아 1..N 연속 번호가 된다("번호 12" 로 지목 가능). HTML/텍스트는 동일한
# 섹션·정렬 순서로 렌더하므로 같은 항목이 양쪽에서 같은 번호를 갖는다.
_ITEM_NO = 0


def _reset_no():
    global _ITEM_NO
    _ITEM_NO = 0


def _next_no():
    global _ITEM_NO
    _ITEM_NO += 1
    return _ITEM_NO


def _esc(s):
    return html.escape(s or "")


def _sentiment(it):
    return it.get("sentiment", "중립")


# ---------------------------------------------------------------------------
# HTML  (기업 보안필터 라이브 진단 결과 — 확인됨:
#        * style="..." 속성은 통째로 삭제된다 → style 을 단 한 개도 쓰지 않는다.
#        * <font size>/<h1> 크기/style font-size 는 전부 무시된다 → 크기로
#          위계를 만들 수 없다(<h2>/<h3> 태그는 시맨틱/굵기용으로만 남긴다).
#        * <font color="#...."> 는 살아남는다(빨간 글자 렌더 확인) → 글자색은
#          오직 <font color> 로만 준다. <font size>/<font face> 는 금지.
#        * bgcolor/border/table/<b>/<ul>/<center>/align 은 살아남는다.
#        위계는 크기 대신 <b> + <font color> + bgcolor 섹션 바 + <center> +
#        <hr>/테두리 행으로 만든다. 색이 다 지워져도 [부정]/[긍정]/[중립]/[앱]
#        텍스트 라벨과 '- 사유:' 접두어로 의미가 완결되게 유지한다.
# ---------------------------------------------------------------------------

# 감정별 '밝은' 셀 배경색 — 검정 글자가 그대로 읽히는 옅은 톤만 사용.
SENT_BG = {"부정": "#f7dede", "긍정": "#dcefdc", "중립": "#e9e9e9"}
BOX_BG = "#eef1f4"  # 섹션 헤더 바 / 요약 박스 / 이슈요약 박스 공통 옅은 회청색

# 감정별 글자색(<font color> 전용) — 필터를 통과하는 강조 수단.
SENT_FG = {"부정": "#c00000", "긍정": "#1a7f37", "중립": "#555555"}
APP_FG = "#1155cc"   # [앱] 라벨 강조색(파랑)
HEAD_FG = "#1f2933"  # 섹션 제목/헤더용 짙은 네이비
# 카테고리 라벨 색: 결제(청록)·챗봇(보라)·기타(회색)
CAT_FG = {"결제": "#0b6b6b", "챗봇": "#7a3fb0", "기타": "#555555"}


def _sent_bg(sent):
    return SENT_BG.get(sent, "#e9e9e9")


def _sent_fg(sent):
    return SENT_FG.get(sent, "#555555")


def _font(color, inner):
    """<font color> 로만 글자색을 준다(size/face 금지 — 필터가 무시/제거)."""
    return f'<font color="{color}">{inner}</font>'


def _row(it, show_type=False, secondary=False, app=False):
    """항목 한 개를 표 '두 행'으로 그린다(콘텐츠 행 + 링크 행).

    - 콘텐츠 행(3열): 출처·날짜 | 감정칩 | 내용. 내용 셀이 남은 폭을 전부 차지해
      긴 URL 이 내용 칸을 찌그러뜨리지 않는다.
    - 링크 행(colspan=3): 표 전체 폭을 한 셀로 써서 '원문: <짧은URL>' 을 옅은 배경 +
      <small> 회색 글자로 자기 줄에 표시. 두 행이 한 항목으로 읽히게 옅게 처리.
    - 감정 칩: 작은 셀에 밝은 bgcolor + [부정]/[긍정]/[중립] 텍스트 라벨(글자색).
    - 내용 셀: 제목=<b>(볼드), 요약(snippet), 사유=빨강 이탤릭('사유:' 라벨).
    - 링크(<a href>)는 기업 보안필터가 통째로 제거하므로 쓰지 않는다. URL 을
      '플레인 텍스트'로만 넣는다(복붙 가능, 필터 생존). 표시 전 _short_url 로 축약.
    - [앱] 라벨과 '사유:' 접두어는 텍스트로 유지(색이 지워져도 의미가 남음).
    style 속성은 전혀 쓰지 않는다.
    """
    no = _next_no()

    sent = _sentiment(it)
    bg = _sent_bg(sent)
    fg = _sent_fg(sent)

    date = f" &middot; {_esc(it['date'])}" if it.get("date") else ""

    # 앱 관련 항목 표식 (색 없이도 [앱] 텍스트가 남음; 색은 파랑 강조)
    app_prefix = f"<b>{_font(APP_FG, '[앱]')}</b> " if app else ""

    type_prefix = ""
    if show_type and it.get("type"):
        type_prefix = f"[{_esc(TYPE_LABEL.get(it['type'], it['type']))}] "

    # 카테고리 라벨({결제}/{챗봇}/{기타}) — 이메일은 비대화형이라 단순 텍스트 태그만.
    cat = it.get("category") or "기타"
    cat_prefix = f"{_font(CAT_FG.get(cat, HEAD_FG), _esc('{' + cat + '}'))} "

    # 제목: 링크가 아니라 볼드 텍스트(링크는 우측 별도 컬럼으로 뺌)
    title = f"<b>{app_prefix}{type_prefix}{cat_prefix}{_esc(it['title'])}</b>"

    # 요약: 내용이 보이도록 표시(있으면)
    snippet = ""
    if it.get("snippet"):
        snippet = f"<br>{_esc(it['snippet'][:120])}"

    # 사유: 제일 유용한 부분 — 빨강 이탤릭 + '사유:' 라벨로 도드라지게
    reason = ""
    if it.get("reason"):
        reason_inner = f"<i>사유: {_esc(it['reason'])}</i>"
        reason = f"<br>{_font('#c00000', reason_inner)}"

    # 링크 행: 노이즈 쿼리를 벗겨 짧아진 URL 을 옅은 배경 + <small> 회색으로.
    link_row = ""
    if it.get("link"):
        short = _short_url(it["link"])
        link_row = (
            f'<tr><td colspan="3" bgcolor="#f5f5f5" valign="top">'
            f'<small>{_font("#777777", f"원문: {_esc(short)}")}</small>'
            f"</td></tr>"
        )

    # 최좌측 번호 셀: 링크 행이 있으면 rowspan=2 로 콘텐츠행+링크행을 세로로 묶어
    # '어느 원문이 어느 내용인지'를 시각적으로 붙인다. 링크가 없으면 행이 하나뿐이라
    # rowspan 없이(단일 행) 둔다. 옅은 회청색(BOX_BG) 배경으로 좌측 거터처럼 도드라지게.
    if link_row:
        num_cell = (
            f'<td width="34" align="center" valign="middle" '
            f'bgcolor="{BOX_BG}" rowspan="2"><b>{no}</b></td>'
        )
    else:
        num_cell = (
            f'<td width="34" align="center" valign="middle" '
            f'bgcolor="{BOX_BG}"><b>{no}</b></td>'
        )

    return f"""
      <tr>
        {num_cell}
        <td width="110" valign="top">{_esc(it['source'])}{date}</td>
        <td width="54" align="center" valign="top" bgcolor="{bg}"><b>{_font(fg, f"[{_esc(sent)}]")}</b></td>
        <td valign="top">{title}{snippet}{reason}</td>
      </tr>{link_row}"""


def _data_table(rows):
    # border="1"(HTML 속성)로 셀 경계를 그린다. cellpadding 으로 여백.
    return f'<table border="1" cellpadding="6" cellspacing="0" width="100%">{rows}</table>'


def _section_bar(title_html):
    """섹션 제목 바: 옅은 bgcolor 셀 + <h3>(굵기) + 짙은 네이비 글자색.
    크기는 필터가 무시하므로 <b>+<font color>+bgcolor 로 위계를 만든다."""
    return (
        f'<tr><td bgcolor="{BOX_BG}"><h3>{_font(HEAD_FG, f"<b>{title_html}</b>")}</h3></td></tr>'
    )


def _spacer(height="14"):
    return f'<tr><td height="{height}">&nbsp;</td></tr>'


def _issue_bulletin_html(summary):
    """'오늘의 이슈 요약' 불레틴. sentiment별 밝은 bgcolor 칩 + [부정]/[긍정]/[중립]
    텍스트 라벨 + <ul>/<li> 불릿 목록. style 없이도 라벨·불릿이 남는다."""
    blocks = []
    for sent in _SENT_ORDER:
        bullets = (summary or {}).get(sent, [])
        if not bullets:
            continue
        bg = _sent_bg(sent)
        fg = _sent_fg(sent)
        lis = "".join(f"<li>{_esc(b)}</li>" for b in bullets)
        blocks.append(
            f'<table border="0" cellpadding="4" cellspacing="0"><tr>'
            f'<td bgcolor="{bg}"><b>{_font(fg, f"[{sent}]")}</b></td></tr></table>'
            f"<ul>{lis}</ul>"
        )
    if not blocks:
        inner = "<p>요약할 신규 소비자여론 없음</p>"
    else:
        inner = "".join(blocks)
    return f"""
      <tr>
        <td>
          <table border="0" cellpadding="12" cellspacing="0" width="100%" bgcolor="{BOX_BG}">
            <tr>
              <td>
                <h3>{_font(HEAD_FG, "<b>&#128204; 오늘의 이슈 요약</b>")}</h3>
                {inner}
              </td>
            </tr>
          </table>
        </td>
      </tr>"""


def _sort_sent(items):
    """섹션 내부 정렬: 부정 → 긍정 → 중립 (그룹 내부는 안정 정렬로 입력 순서 유지)."""
    return sorted(items, key=lambda x: _GROUP_RANK.get(_sentiment(x), 3))


def _category_section(title_html, items, app=False, empty_note=None):
    """카테고리 섹션 한 개: 섹션 바 + 데이터 테이블(부정→긍정→중립).

    - items 가 비어 있고 empty_note 가 None 이면 '' 반환 → 섹션 자체를 생략(클러터 방지).
    - empty_note 가 있으면 항목이 없어도 섹션 바 + 안내문을 항상 표시(예: 📱 앱은 관심영역).
    - 선행 _spacer 를 포함하므로 생략된 섹션은 여백도 남기지 않는다.
    """
    ordered = _sort_sent(items)
    if not ordered:
        if empty_note is None:
            return ""
        body = f"<p>{empty_note}</p>"
    else:
        body = _data_table("".join(_row(i, app=app) for i in ordered))
    return _spacer() + _section_bar(title_html) + f"<tr><td>{body}</td></tr>"


def build_html(items, errors, keywords, issue_summary=None, chatbot_last_date=None, news=None):
    _reset_no()  # 항목 번호를 매 빌드마다 1 부터 다시 시작.
    today = datetime.date.today().strftime("%Y-%m-%d")

    # 날짜 기준 분리: 어제(확정) = 본문, 날짜미상 = 하단 별도 그룹.
    dated = [i for i in items if not i.get("date_unknown")]
    unknown = [i for i in items if i.get("date_unknown")]

    consumer = [i for i in dated if i.get("type", "소비자여론") == "소비자여론"]
    other = [i for i in dated if i.get("type", "소비자여론") != "소비자여론"]

    # 집계는 소비자여론(어제) 전체 대상 — 앱 관련 항목도 포함해서 센다.
    total = len(consumer)
    pos = sum(1 for i in consumer if _sentiment(i) == "긍정")
    neg = sum(1 for i in consumer if _sentiment(i) == "부정")
    neu = total - pos - neg
    neg_pct = f"{neg / total * 100:.0f}%" if total else "0%"

    # --- 섹션 배치 규칙(중복 방지) ---------------------------------------------
    #  한 항목은 정확히 한 섹션에만 실린다. 우선순위(precedence):
    #    1) category==챗봇 → 🤖 챗봇  (앱리뷰든 커뮤니티든 챗봇 피드백은 여기로 — 팀장 핵심주제)
    #    2) app_related    → 📱 앱 관련
    #    3) category==결제 → 💳 결제
    #    4) 그 외          → 그 외(일반)
    bot_items = [i for i in consumer if i.get("category") == "챗봇"]
    rest = [i for i in consumer if i.get("category") != "챗봇"]
    # 앱 관련(챗봇 제외)은 📱 섹션에서 따로 보여주고, 집계용으로도 센다.
    app_items = [i for i in rest if i.get("app_related")]
    app_neg = sum(1 for i in app_items if _sentiment(i) == "부정")
    non_app = [i for i in rest if not i.get("app_related")]
    pay_items = [i for i in non_app if i.get("category") == "결제"]
    etc_items = [i for i in non_app if i.get("category") != "결제"]

    # --- 헤더 (시맨틱 h2; 밝은/무배경 위 검정 글자 — 어두운 배경 금지) ---
    header = f"""
      <tr>
        <td>
          <center><h2>{_font(HEAD_FG, f"<b>데일리 온라인 버즈 &mdash; {_esc(' / '.join(keywords))}</b>")}</h2></center>
          <center><p>{today} &nbsp;&middot;&nbsp; 소비자여론 {total}건 &nbsp;&middot;&nbsp; 기타 {len(other)}건 &nbsp;&middot;&nbsp; 날짜미상 {len(unknown)}건</p></center>
        </td>
      </tr>"""

    # --- 요약 통계 (옅은 박스 + 감정별 밝은 bgcolor 칩; 텍스트로도 완결) ---
    summary = f"""
      <tr>
        <td>
          <table border="0" cellpadding="12" cellspacing="0" width="100%" bgcolor="{BOX_BG}">
            <tr>
              <td>
                <table border="0" cellpadding="6" cellspacing="0">
                  <tr>
                    <td bgcolor="{SENT_BG['긍정']}"><b>{_font(SENT_FG['긍정'], f"긍정 {pos}건")}</b></td>
                    <td>&nbsp;&nbsp;</td>
                    <td bgcolor="{SENT_BG['부정']}"><b>{_font(SENT_FG['부정'], f"부정 {neg}건")}</b></td>
                    <td>&nbsp;&nbsp;</td>
                    <td bgcolor="{SENT_BG['중립']}"><b>{_font(SENT_FG['중립'], f"중립 {neu}건")}</b></td>
                  </tr>
                </table>
                <p>소비자여론 {total}건 &mdash; 부정 비율 <b>{_font(SENT_FG['부정'], neg_pct)}</b></p>
                <p>&#128241; 앱 관련 <b>{_font(APP_FG, f"{len(app_items)}건")}</b> (부정 {_font(SENT_FG['부정'], str(app_neg))})</p>
                <p>&#8251; 소비자여론만 집계 (뉴스/채용/가십 제외) &middot; 앱 관련도 집계에 포함</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>"""

    # --- 📌 오늘의 이슈 요약 (요약통계 바로 아래) ---
    bulletin_section = _issue_bulletin_html(issue_summary)

    # --- 카테고리 섹션 (아티팩트 탭을 정적 스택으로: 💳 결제 → 🤖 챗봇 → 📱 앱 → 그 외) ---
    #  각 섹션 내부는 부정→긍정→중립. 빈 결제/챗봇/그외는 생략(클러터 방지),
    #  📱 앱은 관심영역이라 비어도 '없음' 안내문으로 항상 표시.
    pay_section = _category_section(f"&#128179; 결제 ({len(pay_items)}건)", pay_items)
    # 🤖 챗봇은 팀장 핵심 주제라 0건이어도 항상 표시하고, 0건이면 아카이브의
    #  '가장 최근 챗봇 이력' 날짜를 함께 안내한다("그 날짜 이후 신규 챗봇 버즈 없음").
    bot_note = (
        f"챗봇 0건 (가장 최근 이력: {_esc(chatbot_last_date)})"
        if chatbot_last_date else "챗봇 0건 (최근 이력 없음)"
    )
    bot_section = _category_section(
        f"&#129302; 챗봇 ({len(bot_items)}건)", bot_items, empty_note=bot_note
    )
    app_section = _category_section(
        f"&#128241; 앱 관련 ({len(app_items)}건)", app_items,
        app=True, empty_note="&#128241; 앱 관련 신규 여론 없음",
    )
    etc_section = _category_section(f"&#128221; 그 외 (일반) ({len(etc_items)}건)", etc_items)

    # --- 2차: 뉴스/채용/가십 (항상 보임, h3 바) ---
    other_section = ""
    if other:
        other_rows = "".join(_row(i, show_type=True, secondary=True) for i in other)
        other_section = (
            _spacer()
            + _section_bar(
                f"뉴스 &middot; 채용 &middot; 가십/기타 ({len(other)}건) &mdash; 여론 통계에서 제외"
            )
            + f"<tr><td>{_data_table(other_rows)}</td></tr>"
        )

    # --- 날짜 미상 (별도 그룹, 조용히 버리지 않음; 집계엔 미포함) ---
    unknown_section = ""
    if unknown:
        unknown_rows = "".join(_row(i, show_type=True, secondary=True) for i in unknown)
        unknown_section = (
            _spacer()
            + _section_bar(
                f"날짜 미상 ({len(unknown)}건) &mdash; 게시일 확인 불가, 위 통계에서 제외"
            )
            + f"<tr><td>{_data_table(unknown_rows)}</td></tr>"
        )

    # --- 금융·카드업계 뉴스 (소비자 여론과 별개, 최근 뉴스 정리) ---
    news_section = ""
    if news:
        news_rows = ""
        for i, n in enumerate(news, 1):
            link_line = ""
            if n.get("link"):
                # 소비자여론과 동일 방식: <a> 없이 URL 플레인 텍스트(사내 필터 생존, 복붙 가능)
                link_line = (
                    f'<br><font color="#777777" size="2">원문: {_esc(n["link"])}</font>'
                )
            news_rows += (
                f'<tr><td valign="top" width="26"><b>{i}.</b></td>'
                f'<td><b>{_esc(n.get("title", ""))}</b><br>'
                f'<font color="#888888" size="2">{_esc(n.get("press", ""))} &middot; {_esc(n.get("date", ""))}</font>'
                f'{link_line}</td></tr>'
            )
        news_section = (
            _spacer()
            + _section_bar("&#128240; 금융&middot;카드업계 뉴스 &mdash; 최근 뉴스 정리 (소비자 여론과 별개)")
            + f'<tr><td><table border="0" cellpadding="6" cellspacing="0" width="100%">{news_rows}</table></td></tr>'
        )

    # --- 수집 실패 사이트 (ul/li) ---
    error_section = ""
    if errors:
        lis = "".join(
            f"<li>{_esc(k)}: {_esc(v)}</li>" for k, v in errors.items()
        )
        error_section = (
            _spacer()
            + f"<tr><td><p>수집 실패 사이트:</p><ul>{lis}</ul></td></tr>"
        )

    footer = f"""
      {_spacer("18")}
      <tr>
        <td>
          <p><small>본 리포트는 공개 게시글의 제목&middot;요약&middot;링크만 수집합니다. 원문은 링크에서 확인하세요.</small></p>
        </td>
      </tr>"""

    return f"""<table border="0" cellpadding="16" cellspacing="0" width="100%" bgcolor="#f0f1f3">
  <tr>
    <td align="center">
      <table border="0" cellpadding="20" cellspacing="0" width="640" bgcolor="#ffffff">
        <tr>
          <td>
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
              {header}
              {_spacer()}
              {summary}
              {news_section}
              {_spacer()}
              {bulletin_section}
              {pay_section}
              {bot_section}
              {app_section}
              {etc_section}
              {other_section}
              {unknown_section}
              {error_section}
              {footer}
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


# ---------------------------------------------------------------------------
# 플레인 텍스트 (진짜 안전판: 필터가 HTML 을 뭉개도 이건 읽힌다)
# ---------------------------------------------------------------------------

def _text_item(it, show_type=False, app=False):
    no = _next_no()
    lines = []
    tag = ""
    if app:
        tag += "[앱] "
    if show_type and it.get("type"):
        tag += f"[{TYPE_LABEL.get(it['type'], it['type'])}] "
    src = it.get("source", "")
    date = f", {it['date']}" if it.get("date") else ""
    # category 태그: {결제}/{챗봇}/{기타} — make_artifact 가 파싱해 카테고리 탭 분류.
    # 순서: [type/앱] {category} [sentiment] (source, date) title (round-trip 보장)
    cat = it.get("category") or "기타"
    head = f"{tag}{{{cat}}} [{_sentiment(it)}] ({src}{date}) {it.get('title', '')}".strip()
    lines.append(f"{no}. {head}")
    if it.get("snippet"):
        lines.append(f"   요약: {it['snippet']}")
    if it.get("reason"):
        lines.append(f"   사유: {it['reason']}")
    if it.get("link"):
        lines.append(f"   원문: {_short_url(it['link'])}")
    return "\n".join(lines)


def _text_category(out, title, items, app=False, empty_note=None):
    """플레인텍스트 카테고리 섹션. HTML 의 _category_section 과 동일한 배치/정렬 규칙.
    비어 있고 empty_note 없으면 섹션 생략. 있으면 안내문으로 항상 표시."""
    ordered = _sort_sent(items)
    if not ordered and empty_note is None:
        return
    out.append(f"── {title} ──")
    if ordered:
        for it in ordered:
            out.append(_text_item(it, app=app))
    else:
        out.append(empty_note)
    out.append("")


def build_text(items, errors, keywords, issue_summary=None, chatbot_last_date=None, news=None):
    _reset_no()  # HTML 과 동일한 섹션·정렬 순서로 렌더하므로 같은 번호가 매겨진다.
    today = datetime.date.today().strftime("%Y-%m-%d")

    dated = [i for i in items if not i.get("date_unknown")]
    unknown = [i for i in items if i.get("date_unknown")]

    consumer = [i for i in dated if i.get("type", "소비자여론") == "소비자여론"]
    other = [i for i in dated if i.get("type", "소비자여론") != "소비자여론"]

    total = len(consumer)
    pos = sum(1 for i in consumer if _sentiment(i) == "긍정")
    neg = sum(1 for i in consumer if _sentiment(i) == "부정")
    neu = total - pos - neg
    neg_pct = f"{neg / total * 100:.0f}%" if total else "0%"

    # 섹션 배치 규칙(HTML 과 동일): 우선순위 챗봇 > 앱 > 결제 > 기타. 한 항목은 한 섹션에만.
    #  category==챗봇이면 앱리뷰든 커뮤니티든 🤖 챗봇으로(팀장 핵심주제라 챗봇 피드백을 모은다).
    bot_items = [i for i in consumer if i.get("category") == "챗봇"]
    rest = [i for i in consumer if i.get("category") != "챗봇"]
    app_items = [i for i in rest if i.get("app_related")]
    app_neg = sum(1 for i in app_items if _sentiment(i) == "부정")
    non_app = [i for i in rest if not i.get("app_related")]
    pay_items = [i for i in non_app if i.get("category") == "결제"]
    etc_items = [i for i in non_app if i.get("category") != "결제"]

    out = []
    out.append(f"[데일리 온라인 버즈] {' / '.join(keywords)} — {today}")
    out.append(
        f"소비자여론 {total}건 (긍정 {pos} / 부정 {neg} / 중립 {neu})  부정비율 {neg_pct}"
    )
    out.append(f"앱 관련 {len(app_items)}건 (부정 {app_neg}) — 집계 포함, 아래 [앱] 섹션 참고")
    out.append(f"기타(뉴스/채용/가십) {len(other)}건 — 여론 통계 제외")
    out.append(f"날짜미상 {len(unknown)}건 — 게시일 확인 불가, 통계 제외")
    out.append("")

    if news:
        out.append("── 📰 금융·카드업계 뉴스 (최근 뉴스 정리 — 소비자 여론과 별개) ──")
        for i, n in enumerate(news, 1):
            out.append(f"{i}. {n.get('title','')} ({n.get('press','')} · {n.get('date','')})")
            if n.get("link"):
                out.append(f"   원문: {n['link']}")
        out.append("")

    # 📌 오늘의 이슈 요약
    out.append("── 오늘의 이슈 요약 ──")
    _sum = issue_summary or {}
    if any(_sum.get(s) for s in _SENT_ORDER):
        for sent in _SENT_ORDER:
            for b in _sum.get(sent, []):
                out.append(f"[{sent}] {b}")
    else:
        out.append("요약할 신규 소비자여론 없음")
    out.append("")

    # 카테고리 섹션 (💳 결제 → 🤖 챗봇 → 📱 앱 → 그 외), 내부 부정→긍정→중립
    _text_category(out, "💳 결제", pay_items)
    # 🤖 챗봇은 0건이어도 항상 표시. 0건이면 헤더에 '가장 최근 이력' 날짜를 함께 싣는다.
    if bot_items:
        _text_category(out, "🤖 챗봇", bot_items)
    else:
        if chatbot_last_date:
            out.append(f"── 🤖 챗봇 (0건 · 가장 최근 이력: {chatbot_last_date}) ──")
            out.append(f"챗봇 0건 (가장 최근 이력: {chatbot_last_date})")
        else:
            out.append("── 🤖 챗봇 (0건 · 최근 이력 없음) ──")
            out.append("챗봇 0건 (최근 이력 없음)")
        out.append("")
    _text_category(out, "📱 앱 관련", app_items, app=True, empty_note="앱 관련 신규 여론 없음")
    _text_category(out, "그 외 (일반)", etc_items)

    if other:
        out.append("── 뉴스 · 채용 · 가십/기타 (여론 통계 제외) ──")
        for it in other:
            out.append(_text_item(it, show_type=True))
        out.append("")

    if unknown:
        out.append("── 날짜 미상 (게시일 확인 불가, 통계 제외) ──")
        for it in unknown:
            out.append(_text_item(it, show_type=True))
        out.append("")

    if errors:
        out.append("── 수집 실패 사이트 ──")
        for k, v in errors.items():
            out.append(f"- {k}: {v}")
        out.append("")

    out.append(
        "본 리포트는 공개 게시글의 제목·요약·링크만 수집합니다. 원문은 링크에서 확인하세요."
    )
    return "\n".join(out)
