#!/bin/bash
# GitHub 최신 코드 반영. 각 launchd 작업 시작 시 호출된다.
#  - fast-forward 만 허용(로컬에서 손댄 파일이 있으면 건드리지 않고 기존 코드로 계속)
#  - requirements.txt 가 바뀌면 의존성 재설치
#  --if-stale N : 마지막 업데이트 후 N분이 안 지났으면 건너뜀(5분 주기 작업용)
cd "$(dirname "$0")" || exit 1
STAMP=data/.last_update
mkdir -p data
if [ "$1" = "--if-stale" ] && [ -f "$STAMP" ]; then
  AGE=$(( ( $(date +%s) - $(stat -f %m "$STAMP" 2>/dev/null || stat -c %Y "$STAMP") ) / 60 ))
  [ "$AGE" -lt "${2:-60}" ] && exit 0
fi
touch "$STAMP"
REQ_BEFORE=$(shasum requirements.txt 2>/dev/null | cut -d' ' -f1)
if git -C .. pull --ff-only -q 2>/dev/null; then
  REQ_AFTER=$(shasum requirements.txt 2>/dev/null | cut -d' ' -f1)
  if [ "$REQ_BEFORE" != "$REQ_AFTER" ]; then
    echo "  [업데이트] requirements 변경 → 의존성 재설치"
    .venv/bin/pip install -q -r requirements.txt
  fi
else
  echo "  [업데이트] git pull 실패(오프라인/로컬 변경) — 기존 코드로 진행"
fi
