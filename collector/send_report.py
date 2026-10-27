"""8:00 정각 발송 전용 — 7:30 생성 잡이 만들어둔 오늘자 리포트를 회사메일로 보낸다.
수집·분류는 하지 않는다(그건 생성 잡 몫). 오늘 생성된 파일만 보낸다."""
import datetime
import os
import re
import sys
import time

import config
import mailer
import db

d = datetime.date.today().strftime("%Y-%m-%d")
html_path = os.path.join(config.REPORT_DIR, f"{d}.html")
txt_path = os.path.join(config.REPORT_DIR, f"{d}.txt")

if not os.path.exists(html_path) or not os.path.exists(txt_path):
    print(f"[발송] 오늘자 리포트 없음 → 발송 건너뜀 (생성 잡 미완/실패?)")
    sys.exit(1)

# 어제 파일 재발송 방지: 최근(≤6시간) 생성된 것만.
age_h = (time.time() - os.path.getmtime(html_path)) / 3600
if age_h > 6:
    print(f"[발송] 리포트가 {age_h:.1f}h 전 것(오늘 생성 아님) → 발송 건너뜀")
    sys.exit(1)

html = open(html_path, encoding="utf-8").read()
txt = open(txt_path, encoding="utf-8").read()

# 제목에 넣을 요약(txt 헤더에서 추출)
m = re.search(r"소비자여론\s*(\d+)건.*?부정\s*(\d+)", txt)
subj = f"[버즈리포트] {d}"
if m:
    subj = f"[버즈리포트] {d} — 소비자여론 {m.group(1)}건 (부정 {m.group(2)})"

to = db.resolve_recipients()  # 어드민에서 관리하는 수신자(DB) 우선, 없으면 .env
run_id = db.run_start("send")
try:
    mailer.send(subj, html, txt, to=to)
except Exception as e:
    db.log_send("daily", subj, to, "failed", error=e, report_date=d)
    db.run_finish(run_id, "failed", error=f"{type(e).__name__}: {e}")
    raise
db.log_send("daily", subj, to, "success", report_date=d)
db.run_finish(run_id, "success", summary={"recipients": len(to)})
print(f"[발송] 완료 → {', '.join(to)}")
