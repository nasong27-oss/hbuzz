"""main.py 통합 스모크: 수집기·Claude 를 스텁으로 바꾸고 로컬 DB 로 끝까지 돌린다.
실행: DATABASE_URL=... python -m unittest tests.test_pipeline"""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

TODAY = datetime.date.today()


def fake_collect(keywords, enabled=None):
    import collectors
    collectors.SOURCE_STATS.clear()
    collectors.SOURCE_STATS["클리앙"] = {"count": 3, "secs": 1.0, "tries": 1, "error": ""}
    d = TODAY.strftime("%Y.%m.%d")
    return ([
        {"source": "클리앙", "title": "현대카드 앱 로그인 오류", "link": "https://t/1", "snippet": "", "date": d},
        {"source": "클리앙", "title": "현카 애플페이 좋아요", "link": "https://t/2", "snippet": "", "date": d},
        {"source": "클리앙", "title": "현대 자동차 카드 무관", "link": "https://t/3", "snippet": "", "date": d},
    ], {"더쿠": "0건"})


def fake_claude(system, prompt, tool, desc, schema, max_tokens=4096):
    import classify
    classify.STATS["calls"] += 1
    if tool == "classify_posts":
        return {"results": [
            {"idx": 0, "relevant": True, "type": "소비자여론", "category": "기타", "sentiment": "부정",
             "app_related": True, "reason": "로그인 오류"},
            {"idx": 1, "relevant": True, "type": "소비자여론", "category": "결제", "sentiment": "긍정",
             "app_related": False, "reason": "애플페이 만족"}]}
    if tool == "submit_summary":
        return {"부정": ["앱 로그인 오류"], "긍정": ["애플페이 만족"], "중립": []}
    raise RuntimeError("unexpected")


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL 필요")
class PipelineTest(unittest.TestCase):
    def test_end_to_end(self):
        import classify, collectors, db, main
        tmp = tempfile.mkdtemp()
        config.ANTHROPIC_API_KEY = "x"
        with mock.patch.object(collectors, "collect_all", fake_collect), \
             mock.patch.object(collectors, "finance_news", lambda seen=None: []), \
             mock.patch.object(classify, "call_claude", fake_claude), \
             mock.patch.object(config, "REPORT_DIR", tmp), \
             mock.patch.object(main, "SEEN_PATH", os.path.join(tmp, "seen.json")), \
             mock.patch.object(main, "SEEN_NEWS_PATH", os.path.join(tmp, "sn.json")), \
             mock.patch.object(main, "ARCHIVE_PATH", os.path.join(tmp, "a.jsonl")), \
             mock.patch.object(sys, "argv", ["main.py", "--dry-run"]):
            main.main()
        import psycopg
        with psycopg.connect(config.DATABASE_URL) as c:
            rep = c.execute("SELECT subject, stats->>'consumer', errors FROM reports WHERE report_date=%s",
                            (TODAY,)).fetchone()
            run = c.execute("SELECT status, sources, claude FROM runs WHERE kind='generate' "
                            "ORDER BY id DESC LIMIT 1").fetchone()
            n = c.execute("SELECT count(*) FROM items WHERE report_date=%s AND link LIKE 'https://t/%%'",
                          (TODAY,)).fetchone()[0]
        self.assertIn("소비자여론 2건", rep[0])
        self.assertEqual(rep[1], "2")
        self.assertIn("더쿠", rep[2])
        self.assertEqual(run[0], "success")
        self.assertIn("클리앙", run[1])
        self.assertEqual(n, 2)  # '현대 자동차 카드' 는 관련성 필터에서 탈락
        self.assertTrue(os.path.exists(os.path.join(tmp, f"{TODAY}.html")))


if __name__ == "__main__":
    unittest.main()
