#!/bin/bash
# 07:30 launchd 호출 — 수집·분류·리포트 생성만(이메일 발송은 08:00 별도 잡).
export TZ=Asia/Seoul
cd "$(dirname "$0")" || exit 1
mkdir -p data
# 실행 전 GitHub 최신 코드 반영(실패해도 기존 코드로 계속)
./update.sh >> data/launchd.log 2>&1 || true
echo "===== $(date '+%Y-%m-%d %H:%M:%S') [생성] 시작 =====" >> data/launchd.log

# 깨어난 직후 네트워크 대기(수집에 필요).
# 2026-09-16: 120초(40x3s)로는 기상 직후 네트워크가 안 붙어, 수집이 오프라인 상태로 시작돼
# 재시도만 반복하다 생성이 73분 걸렸고 08:00 발송이 리포트를 못 찾아 스킵됐다.
# → 대기를 10분(100x6s)으로 늘리고, 끝내 실패하면 로그에 명시해 원인 추적이 되게 한다.
NET_OK=0
for i in $(seq 1 100); do
  if nc -z -w4 smtp.gmail.com 587 2>/dev/null && curl -sI -m4 -o /dev/null https://www.google.com 2>/dev/null; then
    echo "  네트워크 준비됨 (${i}회, $(date '+%H:%M:%S'))" >> data/launchd.log
    NET_OK=1; break
  fi
  sleep 6
done
[ "$NET_OK" = "1" ] || echo "  ⚠ 네트워크 대기 10분 초과 — 오프라인 상태로 진행(수집 품질 저하 가능)" >> data/launchd.log

# --dry-run: 리포트 생성·중복제거·아카이브까지 하되 이메일은 보내지 않는다.
caffeinate -s -i .venv/bin/python main.py --dry-run >> data/launchd.log 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') [생성] 종료 (exit=$?) =====" >> data/launchd.log
