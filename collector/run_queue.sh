#!/bin/bash
# 5분마다 launchd 호출 — 웹에서 들어온 '새로 수집 요청'을 처리한다.
export TZ=Asia/Seoul
cd "$(dirname "$0")" || exit 1
mkdir -p data
LOG=data/queue.log
# 로그가 2MB 넘으면 비운다(5분 주기라 쌓이기 쉬움)
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 2000000 ] && : > "$LOG"
# 중복 실행 방지(이전 회차가 아직 수집 중이면 종료)
LOCK=data/.queue.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  # 2시간 넘은 잠금은 죽은 것으로 보고 해제
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then rmdir "$LOCK"; mkdir "$LOCK" || exit 0; else exit 0; fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
./update.sh --if-stale 60 >> "$LOG" 2>&1 || true
caffeinate -s -i .venv/bin/python queue_worker.py >> "$LOG" 2>&1
