"""클라우드 백업 테스트: api/cloud.py 핸들러를 로컬 HTTP 서버로 띄워 인증·진단·백업 실행을 검증.
실행: DATABASE_URL=... python -m unittest tests.test_cloud"""
import base64
import datetime
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "api"))
os.environ["AUTH_SECRET"] = "unit-test-secret-123456"
os.environ["HBUZZ_DATA_DIR"] = "/tmp/hbuzz-test"

import config  # noqa: E402
import cloud as api_cloud  # noqa: E402  (api/cloud.py — collector 에는 cloud.py 가 없어 충돌 없음)
import collectors  # noqa: E402
import classify  # noqa: E402
import db  # noqa: E402


def fake_claude(system, prompt, tool, desc, schema, max_tokens=4096):
    if tool == "classify_posts":
        n = prompt.count('"idx"')
        return {"results": [{"idx": i, "relevant": True, "type": "소비자여론", "category": "기타",
                             "sentiment": "부정", "app_related": True, "reason": "오류"} for i in range(n)]}
    return {"부정": ["앱 오류"], "긍정": [], "중립": []}

TODAY = datetime.date.today()


def token(role, secret="unit-test-secret-123456", exp=None):
    exp = exp or int(time.time()) + 3600
    sig = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), f"{role}.{exp}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return f"{role}.{exp}.{sig}"


def call(port, method, path, cookie=None, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    if cookie:
        req.add_header("Cookie", f"hb_session={cookie}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def slow(kw):
    time.sleep(3)
    return [{"source": "느린곳", "title": f"현대카드 {kw}", "link": f"https://slow/{kw}", "snippet": "",
             "date": TODAY.strftime("%Y.%m.%d")}]


def fast(kw):
    return [{"source": "클리앙", "title": f"현대카드 앱 {kw} 오류", "link": f"https://fast/{kw}",
             "snippet": "", "date": TODAY.strftime("%Y.%m.%d")}]


def boom(kw):
    raise RuntimeError("403 Forbidden")


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL 필요")
class CloudTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), api_cloud.handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        import psycopg
        cls.conn = psycopg.connect(config.DATABASE_URL, autocommit=True)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.conn.execute("DELETE FROM reports WHERE report_date=%s", (TODAY,))
        self.conn.execute("DELETE FROM items WHERE link LIKE 'https://fast/%%' OR link LIKE 'https://slow/%%'")
        self.conn.execute("DELETE FROM diagnostics")

    def test_auth(self):
        self.assertEqual(call(self.port, "GET", "/api/cloud?action=diag")[0], 401)
        self.assertEqual(call(self.port, "GET", "/api/cloud?action=diag", token("user"))[0], 401)
        self.assertEqual(call(self.port, "GET", "/api/cloud?action=diag", token("admin", "wrong-secret-xxxxxx"))[0], 401)
        self.assertEqual(call(self.port, "GET", "/api/cloud?action=diag",
                              token("admin", exp=int(time.time()) - 5))[0], 401)

    def test_diag_saves(self):
        reg = {"클리앙": fast, "뽐뿌": boom}
        with mock.patch.object(collectors, "COLLECTORS", reg), \
             mock.patch.object(collectors, "_fetch_news_feed", lambda q, e: []):
            code, res = call(self.port, "GET", "/api/cloud?action=diag", token("admin"))
        self.assertEqual(code, 200, res)
        r = res["report"]["results"]
        self.assertEqual(r["클리앙"]["status"], "ok")
        self.assertEqual(r["뽐뿌"]["status"], "error")
        self.assertEqual(db.latest_diagnosis("vercel-icn1")["env"], "vercel-icn1")

    def test_backup_run_deadline_and_skip(self):
        # 진단상 '뽐뿌'는 차단 → 건너뜀, '느린곳'은 마감에 걸려 일부만
        self.conn.execute("INSERT INTO diagnostics (env, results) VALUES ('vercel-icn1', %s)", (json.dumps(
            {"env": "vercel-icn1", "results": {"뽐뿌": {"status": "blocked", "detail": "HTTP 403"}}}),))
        reg = {"클리앙": fast, "느린곳": slow, "뽐뿌": boom}
        import cloud_backup as core
        with mock.patch.object(collectors, "COLLECTORS", reg), \
             mock.patch.object(collectors, "finance_news", lambda seen=None: []), \
             mock.patch.object(classify, "call_claude", fake_claude), \
             mock.patch.object(core, "COLLECT_DEADLINE", 4), \
             mock.patch.object(db, "resolve_keywords", lambda: ["a", "b", "c"]), \
             mock.patch.object(config, "ANTHROPIC_API_KEY", "x"):
            code, res = call(self.port, "POST", "/api/cloud?action=run", token("admin"), {"send": False})
        self.assertEqual(code, 200, res)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["stats"]["sources_skipped"], ["뽐뿌"])
        self.assertIn("클리앙", res["ok_sources"])
        rep = self.conn.execute("SELECT subject, errors, stats->>'backup' FROM reports WHERE report_date=%s",
                                (TODAY,)).fetchone()
        self.assertTrue(rep[0].startswith("[버즈리포트 백업]"))
        self.assertIn("백업 수집", rep[1])
        self.assertIn("뽐뿌", rep[1])
        self.assertIn("느린곳", rep[1])  # 마감 표시
        self.assertEqual(rep[2], "true")
        self.assertNotIn("Claude 분류", rep[1])  # 병렬 분류도 폴백 없이
        kinds = self.conn.execute("SELECT kind, status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(kinds, ("cloud", "success"))

        # Mac 리포트가 있으면 백업은 실행하지 않는다
        self.conn.execute("UPDATE reports SET stats = stats || '{\"backup\": false}' WHERE report_date=%s", (TODAY,))
        code, res = call(self.port, "POST", "/api/cloud?action=run", token("admin"), {})
        self.assertFalse(res["ok"])
        self.assertIn("이미", res["reason"])


if __name__ == "__main__":
    unittest.main()
