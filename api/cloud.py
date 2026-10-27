"""Vercel Python 함수 — 클라우드 진단·백업 수집 (/api/cloud)

  GET  /api/cloud?action=diag                → 사이트별 수집 진단(Vercel 서울) 후 DB 저장
  POST /api/cloud?action=run  {"send": bool, "force": bool}
                                             → 백업 리포트 생성(+선택 발송)

관리자 세션 쿠키(hb_session, 웹과 같은 HMAC 서명)가 있어야 한다.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collector"))
os.environ.setdefault("HBUZZ_DATA_DIR", "/tmp/hbuzz")


def verify_admin(cookie_header):
    secret = os.environ.get("AUTH_SECRET", "")
    if len(secret) < 16 or not cookie_header:
        return False
    c = SimpleCookie()
    try:
        c.load(cookie_header)
    except Exception:
        return False
    tok = c.get("hb_session")
    if not tok:
        return False
    parts = tok.value.split(".")
    if len(parts) != 3:
        return False
    role, exp, sig = parts
    if role != "admin" or not exp.isdigit() or int(exp) < time.time():
        return False
    mac = hmac.new(secret.encode(), f"{role}.{exp}".encode(), hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    return hmac.compare_digest(expected, sig)


class handler(BaseHTTPRequestHandler):
    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _action(self):
        return (parse_qs(urlsplit(self.path).query).get("action") or [""])[0]

    def _guard(self):
        if not verify_admin(self.headers.get("Cookie")):
            self._json(401, {"ok": False, "reason": "관리자 로그인이 필요합니다."})
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        if self._action() != "diag":
            return self._json(404, {"ok": False, "reason": "알 수 없는 요청"})
        try:
            import cloud_backup as cloud
            rep = cloud.run_diagnosis()
            self._json(200, {"ok": True, "report": rep})
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"ok": False, "reason": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        if not self._guard():
            return
        if self._action() != "run":
            return self._json(404, {"ok": False, "reason": "알 수 없는 요청"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            opts = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError:
            opts = {}
        try:
            import cloud_backup as cloud
            res = cloud.run_backup(send=bool(opts.get("send")), force=bool(opts.get("force")))
            self._json(200, res)
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"ok": False, "reason": f"{type(e).__name__}: {e}"})

    def log_message(self, *a):  # 기본 stderr 접근 로그 끄기
        pass
