"""Claude 분류기 오프라인 테스트 (API 호출은 목킹)."""
import json
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
        os.environ["CLASSIFIER"] = "api"
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


def _cc_proc(structured=None, rc=0, is_error=False, result="", cost=0.01):
    out = {"type": "result", "is_error": is_error, "result": result, "total_cost_usd": cost,
           "usage": {"input_tokens": 50, "output_tokens": 10}}
    if structured is not None:
        out["structured_output"] = structured
    return mock.Mock(returncode=rc, stdout=json.dumps(out, ensure_ascii=False), stderr="")


class ClaudeCodeTest(unittest.TestCase):
    """구독(Claude Code) 우선 → API 키 폴백 경로."""

    def setUp(self):
        os.environ["CLASSIFIER"] = "auto"
        config.ANTHROPIC_API_KEY = "test"
        classify.reset_stats()
        classify.API_BACKOFF = 0
        self.bin = mock.patch.object(classify, "claude_bin", return_value="/usr/local/bin/claude")
        self.bin.start()

    def tearDown(self):
        self.bin.stop()

    def _ok(self, n):
        return {"results": [{"idx": i, "relevant": True, "type": "소비자여론", "category": "기타",
                             "sentiment": "중립", "app_related": False, "reason": "r"} for i in range(n)]}

    def test_uses_subscription_without_api(self):
        items = [{"title": "현대카드 a"}, {"title": "현카 b"}]
        with mock.patch.object(classify.subprocess, "run", return_value=_cc_proc(self._ok(2))) as run, \
             mock.patch.object(classify.requests, "post") as post:
            classify.annotate(items)
        post.assert_not_called()
        cmd = run.call_args.args[0]
        self.assertIn("--json-schema", cmd)
        self.assertNotIn("--bare", cmd)  # bare 는 구독 로그인을 안 씀
        self.assertEqual(cmd[cmd.index("--model") + 1], config.CLAUDE_MODEL)
        self.assertNotIn("ANTHROPIC_API_KEY", run.call_args.kwargs["env"])  # API 과금 방지
        self.assertEqual(classify.STATS["via"], {"claude-code": 1, "api": 0})
        self.assertFalse(items[0]["fallback"])

    def test_falls_back_to_api_and_breaks_circuit(self):
        items = [{"title": f"현대카드 {i}"} for i in range(20)]  # 배치 2개
        api_data = lambda n: {"content": [{"type": "tool_use", "name": "classify_posts", "input": self._ok(n)}],
                              "usage": {}}
        responses = [_resp(200, api_data(15)), _resp(200, api_data(5))]
        with mock.patch.object(classify.subprocess, "run",
                               return_value=_cc_proc(rc=1, is_error=True, result="usage limit reached")) as run, \
             mock.patch.object(classify.requests, "post", side_effect=responses):
            classify.annotate(items)
        self.assertEqual(run.call_count, 1)  # 첫 실패 후 구독 경로 재시도 안 함
        self.assertEqual(classify.STATS["via"], {"claude-code": 0, "api": 2})
        self.assertIn("usage limit", classify.STATS["cc_disabled"])
        self.assertFalse(any(i["fallback"] for i in items))

    def test_no_binary_goes_api(self):
        self.bin.stop()
        with mock.patch.object(classify, "claude_bin", return_value=None), \
             mock.patch.object(classify.requests, "post", return_value=_resp(
                 200, {"content": [{"type": "tool_use", "name": "classify_posts", "input": self._ok(1)}]})):
            items = [{"title": "현대카드"}]
            classify.annotate(items)
        self.bin.start()
        self.assertEqual(classify.STATS["via"]["api"], 1)

    def test_subscription_only_without_api_key(self):
        config.ANTHROPIC_API_KEY = ""
        self.assertTrue(classify.ai_available())
        items = [{"title": "현대카드"}]
        with mock.patch.object(classify.subprocess, "run", return_value=_cc_proc(self._ok(1))):
            classify.annotate(items)
        self.assertFalse(items[0]["fallback"])

    def test_both_fail_keyword_fallback(self):
        items = [{"title": "현대카드 최악"}]
        with mock.patch.object(classify.subprocess, "run", side_effect=classify.subprocess.TimeoutExpired("claude", 1)), \
             mock.patch.object(classify.requests, "post", return_value=_resp(401, {})):
            classify.annotate(items)
        self.assertTrue(items[0]["fallback"])
        self.assertEqual(len(classify.STATS["errors"]), 2)


if __name__ == "__main__":
    unittest.main()
