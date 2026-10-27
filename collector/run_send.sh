#!/bin/bash
# 08:00 launchd 호출 — 07:30 생성 잡이 만들어둔 오늘자 리포트를 정각에 발송.
#
# 자가치유(2026-09-16 장애 대응): 예전엔 08:00에 리포트가 없으면 그냥 "건너뜀"으로 끝나
# 그날 메일이 아예 안 나갔다(기상 직후 네트워크 지연 → 생성이 73분 걸림). 이제는
#   ① 생성 잡이 아직 돌고 있으면 최대 40분까지 기다렸다가 발송하고,
#   ② 생성이 죽어서 리포트가 없으면 여기서 직접 생성한 뒤 발송한다.
# 어떤 경로로든 '그날 메일이 안 나가는 상황'을 없애는 게 목적이다.
export TZ=Asia/Seoul
cd "$(dirname "$0")" || exit 1
mkdir -p data
# 실행 전 GitHub 최신 코드 반영(실패해도 기존 코드로 계속)
./update.sh >> data/launchd.log 2>&1 || true
LOG=data/launchd.log
echo "===== $(date '+%Y-%m-%d %H:%M:%S') [발송] 시작 =====" >> "$LOG"

TODAY="$(date '+%Y-%m-%d')"
REPORT="reports/${TODAY}.html"

# 1) 네트워크 대기(SMTP). 기상 직후 DNS/연결이 늦게 붙는 경우 대비해 5분까지 기다린다.
for i in $(seq 1 50); do
  nc -z -w4 smtp.gmail.com 587 2>/dev/null && break
  sleep 6
done

# 2) 오늘자 리포트 대기 — 생성 잡이 아직 수집/분류 중일 수 있다(최대 40분, 30초 간격).
WAITED=0
while [ ! -f "$REPORT" ] && [ "$WAITED" -lt 2400 ]; do
  # 생성 잡은 cwd 기준 상대경로로 뜬다: '.venv/bin/python main.py --dry-run'
  if pgrep -if "python main\.py" >/dev/null 2>&1; then
    [ $((WAITED % 300)) -eq 0 ] && echo "  생성 잡 진행 중 — 리포트 대기(${WAITED}s)" >> "$LOG"
  else
    # 생성 프로세스가 없는데 리포트도 없다 → 생성이 실패/미실행. 여기서 직접 만든다.
    echo "  ⚠ 리포트 없음 + 생성 잡도 없음 → 발송 잡이 직접 생성 시작" >> "$LOG"
    caffeinate -s -i .venv/bin/python main.py --dry-run >> "$LOG" 2>&1
    break
  fi
  sleep 30
  WAITED=$((WAITED + 30))
done

# 3) 발송(send_report.py 는 자체 SMTP 재시도 보유). 리포트가 끝내 없으면 스킵 로그를 남긴다.
if [ -f "$REPORT" ]; then
  caffeinate -s -i .venv/bin/python send_report.py >> "$LOG" 2>&1
else
  echo "  ⚠ 40분 대기 후에도 리포트 없음 — 발송 불가(원인 확인 필요)" >> "$LOG"
fi
echo "===== $(date '+%Y-%m-%d %H:%M:%S') [발송] 종료 (exit=$?) =====" >> "$LOG"
