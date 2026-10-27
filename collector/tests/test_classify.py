"""Claude 분류기 오프라인 테스트 (API 호출은 목킹)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import classify  # noqa: E402


def _resp(status, body):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = body
    r.text = str(body)
    return r


def _tool(name, data):
    return {"content": [{"type": "tool_use", "name": name, "input": data}],
            "usage": {"input_tokens": 100, "output_tokens": 20}}


class ClassifyTest(unittest.TestCase):
    def setUp(self):
        config.ANTHROPIC_API_KEY = "test"
        classify.reset_stats()
        classify.API_BACKOFF = 0

    def test_annotate_ok_out_of_order(self):
        items = [{"title": "현대카드 앱 로그인 오류", "snippet": ""},
                 {"title": "현카 애플페이 좋네요", "snippet": ""}]
        data = {"results": [
            {"idx": 1, "relevant": True, "type": "소비자여론", "category": "결제",
             "sentiment": "긍정", "app_related": False, "reason": "애플페이 만족"},
            {"idx": 0, "relevant": True, "type": "소비자여론", "category": "기타",
             "sentiment": "부정", "app_related": True, "reason": "로그인 오류 불만"}]}
        with mock.patch.object(classify.requests, "post",
                               return_value=_resp(200, _tool("classify_posts", data))) as p:
            classify.annotate(items)
        body = p.call_args.kwargs["json"]
        self.assertEqual(body["model"], config.CLAUDE_MODEL)
        self.assertEqual(body["tool_choice"]["name"], "classify_posts")
        self.assertEqual(items[0]["sentiment"], "부정")
        self.assertTrue(items[0]["app_related"])
        self.assertEqual(items[1]["category"], "결제")
        self.assertEqual(items[1]["score"], 1)
        self.assertFalse(items[0]["fallback"])
        self.assertEqual(classify.STATS["fallback"], 0)
        self.assertEqual(classify.STATS["input_tokens"], 100)

    def test_annotate_fallback_on_auth_error(self):
        items = [{"title": "현대카드 최악 먹통", "snippet": ""}]
        with mock.patch.object(classify.requests, "post",
                               return_value=_resp(401, {"error": "bad key"})) as p:
            classify.annotate(items)
        self.assertEqual(p.call_count, 1)  # 401 은 재시도 안 함
        self.assertTrue(items[0]["fallback"])
        self.assertEqual(classify.STATS["fallback"], 1)
        self.assertTrue(classify.STATS["errors"])

    def test_retry_then_success(self):
        items = [{"title": "현대카드", "snippet": ""}]
        ok = _tool("classify_posts", {"results": [
            {"idx": 0, "relevant": False, "type": "가십기타", "category": "기타",
             "sentiment": "중립", "app_related": False, "reason": "무관"}]})
        with mock.patch.object(classify.requests, "post",
                               side_effect=[_resp(529, {}), _resp(200, ok)]):
            classify.annotate(items)
        self.assertFalse(items[0]["relevant"])
        self.assertFalse(items[0]["fallback"])

    def test_missing_result_falls_back(self):
        items = [{"title": "a"}, {"title": "b"}]
        data = {"results": [{"idx": 0, "relevant": True, "type": "소비자여론",
                             "category": "기타", "sentiment": "중립",
                             "app_related": False, "reason": ""}]}
        with mock.patch.object(classify.requests, "post",
                               return_value=_resp(200, _tool("classify_posts", data))):
            classify.annotate(items)
        self.assertTrue(all(i["fallback"] for i in items))

    def test_news_and_summary(self):
        news = {"results": [{"idx": 0, "relevant": False, "ko": "x"},
                            {"idx": 1, "relevant": True, "ko": "애플페이 확대"}]}
        with mock.patch.object(classify.requests, "post",
                               return_value=_resp(200, _tool("curate_news", news))):
            out = classify.curate_news([{"title": "a"}, {"title": "Apple Pay expands"}])
        self.assertEqual(out, [{"relevant": False, "ko": "x"},
                               {"relevant": True, "ko": "애플페이 확대"}])
        summ = {"부정": ["로그인 오류"], "긍정": [["애플페이"]], "중립": []}
        with mock.patch.object(classify.requests, "post",
                               return_value=_resp(200, _tool("submit_summary", summ))):
            out = classify.summarize_issues([{"sentiment": "부정", "title": "t"}])
        self.assertEqual(out["긍정"], ["애플페이"])

    def test_no_key(self):
        config.ANTHROPIC_API_KEY = ""
        items = [{"title": "현대카드 좋아요"}]
        classify.annotate(items)
        self.assertTrue(items[0]["fallback"])


if __name__ == "__main__":
    unittest.main()
