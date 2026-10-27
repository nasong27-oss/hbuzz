"""키워드 사전 기반 긍부정 분류 (무료, 대략 70% 정확도).

제목+요약 텍스트에서 긍정/부정 단어를 스코어링한다.
나중에 Claude API로 교체할 수 있도록 classify(text) -> (label, score) 인터페이스 유지.
"""

NEGATIVE = [
    "개악", "혜택 축소", "축소", "단종", "짜증", "최악", "실망", "불만", "화나",
    "어이없", "황당", "먹통", "장애", "오류", "안됨", "안돼", "거절", "한도 축소",
    "연회비 아깝", "해지", "탈회", "손해", "사기", "불편", "느리", "개선 좀",
    "별로", "쓰레기", "호갱", "뒤통수", "기습", "일방적", "강제", "누락", "미적립",
    "상담원 연결", "고객센터 불통", "환불 거부", "수수료 인상",
]

POSITIVE = [
    "혜자", "역대급", "개이득", "이득", "만족", "좋네", "좋아요", "추천", "최고",
    "괜찮", "든든", "유용", "꿀", "혜택 좋", "잘 쓰고", "잘쓰고", "편하", "편리",
    "빠르", "친절", "감사", "성공", "발급 완료", "재발급", "메인카드", "주력카드",
    "적립 잘", "할인 잘", "예쁘", "디자인 좋",
]


def classify(text):
    """(label, score) 반환. label ∈ {긍정, 부정, 중립}, score = 긍정단어수 - 부정단어수."""
    t = (text or "").replace(" ", "")
    pos = sum(1 for w in POSITIVE if w.replace(" ", "") in t)
    neg = sum(1 for w in NEGATIVE if w.replace(" ", "") in t)
    score = pos - neg
    if score > 0:
        return "긍정", score
    if score < 0:
        return "부정", score
    return "중립", 0


def annotate(items):
    for it in items:
        label, score = classify(f"{it.get('title', '')} {it.get('snippet', '')}")
        it["sentiment"] = label
        it["score"] = score
    return items
