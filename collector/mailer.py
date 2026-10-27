"""Gmail SMTP 발송."""
import smtplib
import socket
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import config

# 잠자기에서 깨어난 직후엔 WiFi/DNS 가 아직 안 붙어 발송이 실패할 수 있다
# (Errno 8 nodename..., Errno 54 connection reset 등). 일시적 네트워크 오류는
# 몇 초 간격으로 재시도한다.
_RETRIES = 6
_BACKOFF = 10  # 초 (10,20,30,40,50 → 최대 ~2.5분 대기)
_RETRYABLE = (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
              socket.gaierror, socket.timeout, ConnectionError, OSError)


def send(subject, html_body, text_body=None, to=None):
    to = to or config.REPORT_TO
    if not (config.GMAIL_USER and config.GMAIL_APP_PASSWORD and to):
        raise RuntimeError(
            ".env에 GMAIL_APP_PASSWORD 또는 REPORT_TO가 비어 있어 메일을 보낼 수 없습니다."
        )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.GMAIL_USER
    msg["To"] = ", ".join(to)
    # multipart/alternative 에선 뒤에 붙인 파트가 '더 선호'된다.
    # 보안필터가 HTML 을 뭉개도 클라이언트가 텍스트본으로 폴백할 수 있도록
    # text/plain 을 먼저, text/html 을 나중에 attach 한다.
    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    raw = msg.as_string()

    last_err = None
    for attempt in range(1, _RETRIES + 1):
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
                server.sendmail(config.GMAIL_USER, to, raw)
            if attempt > 1:
                print(f"메일 발송: {attempt}회차에 성공")
            return
        except _RETRYABLE as e:
            last_err = e
            if attempt < _RETRIES:
                wait = _BACKOFF * attempt
                print(f"메일 발송 {attempt}회차 실패({type(e).__name__}: {e}) — {wait}초 후 재시도")
                time.sleep(wait)
            # 인증 실패(SMTPAuthenticationError 등)는 재시도해도 소용없으니 그대로 raise 된다.
    raise last_err
