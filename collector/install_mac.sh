#!/bin/bash
# hbuzz 수집기 — 집 Mac 원클릭 설치
#
#   git clone https://github.com/nasong27-oss/hbuzz.git ~/hbuzz
#   ~/hbuzz/collector/install_mac.sh
#
# 하는 일: 파이썬 환경 → .env 입력 → DB 테이블 생성 → launchd 3개 등록 → 잠자기 설정 안내
# 여러 번 실행해도 안전하다(기존 .env 는 유지, launchd 는 다시 등록).
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
AGENTS="$HOME/Library/LaunchAgents"
say() { printf "\n\033[1m▶ %s\033[0m\n" "$1"; }

case "$DIR" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
    echo "⚠ $DIR 는 macOS 보호 폴더라 launchd 가 실행하지 못합니다. ~/hbuzz 처럼 홈 바로 아래에 두세요."
    exit 1;;
esac

say "1/5 파이썬 환경"
command -v python3 >/dev/null || { echo "python3 가 없습니다. 'xcode-select --install' 후 다시 실행하세요."; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Python 3.9 이상이 필요합니다."; exit 1; }
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
mkdir -p data reports
echo "설치 완료"

say "2/5 비밀값(.env)"
if [ -f .env ]; then
  echo ".env 가 이미 있어 그대로 둡니다. 바꾸려면 'nano $DIR/.env'"
else
  cp .env.example .env
  ask() { # $1=키 $2=설명 $3=숨김여부
    local v
    if [ "$3" = "secret" ]; then read -r -s -p "$2: " v; echo; else read -r -p "$2: " v; fi
    [ -n "$v" ] && python3 - "$1" "$v" <<'PY'
import sys, re
k, v = sys.argv[1], sys.argv[2]
p = ".env"; s = open(p, encoding="utf-8").read()
s = re.sub(rf"^{re.escape(k)}=.*$", f"{k}={v}", s, flags=re.M) if re.search(rf"^{re.escape(k)}=", s, re.M) else s + f"\n{k}={v}\n"
open(p, "w", encoding="utf-8").write(s)
PY
  }
  echo "엔터로 건너뛴 값은 나중에 .env 에서 채울 수 있습니다."
  ask GMAIL_APP_PASSWORD "nasong27@gmail.com 앱 비밀번호(16자리)" secret
  ask ANTHROPIC_API_KEY "Anthropic API 키(sk-ant-...)" secret
  ask DATABASE_URL "Neon DATABASE_URL(postgresql://...)" secret
  ask REPORT_TO "기본 수신자(웹에 수신자를 등록하면 그쪽이 우선, 쉼표 구분)"
  chmod 600 .env
fi

say "3/5 DB 연결 확인·테이블 생성"
if grep -qE '^DATABASE_URL=.+' .env; then
  .venv/bin/python db.py init || echo "⚠ DB 연결 실패 — .env 의 DATABASE_URL 확인 후 '.venv/bin/python db.py init' 재실행"
else
  echo "DATABASE_URL 이 없어 건너뜀(웹 대시보드 연동 안 됨)"
fi

say "4/5 자동 실행 등록(launchd)"
mkdir -p "$AGENTS"
# 예전 설치(~/hyundaibuzz)가 이 Mac 에 있으면 중복 발송되므로 끈다
for old in com.hyundaibuzz.generate com.hyundaibuzz.send com.hyundaibuzz.daily; do
  if [ -f "$AGENTS/$old.plist" ]; then
    launchctl unload "$AGENTS/$old.plist" 2>/dev/null || true
    mv "$AGENTS/$old.plist" "$AGENTS/$old.plist.disabled"
    echo "기존 잡 $old 비활성화(중복 발송 방지)"
  fi
done

plist() { # $1=라벨 $2=스크립트 $3=일정 XML
  cat > "$AGENTS/$1.plist" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/$2</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  $3
  <key>StandardOutPath</key><string>$DIR/data/launchd.stdout.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/launchd.stderr.log</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
</dict>
</plist>
XML
  launchctl unload "$AGENTS/$1.plist" 2>/dev/null || true
  launchctl load "$AGENTS/$1.plist"
  echo "등록: $1"
}
plist com.hbuzz.generate run_generate.sh "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>"
plist com.hbuzz.send run_send.sh "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>"
plist com.hbuzz.queue run_queue.sh "<key>StartInterval</key><integer>300</integer><key>RunAtLoad</key><true/>"

say "5/5 잠자기·정전 대비(관리자 비밀번호 필요)"
echo "  sudo pmset -a disablesleep 1   # 뚜껑 닫아도 계속 실행"
echo "  sudo pmset -a autorestart 1    # 정전 후 전원 복구 시 자동 부팅"
read -r -p "지금 적용할까요? [y/N] " yn
if [ "$yn" = "y" ] || [ "$yn" = "Y" ]; then
  sudo pmset -a disablesleep 1 && sudo pmset -a autorestart 1 && echo "적용 완료"
fi

cat <<EOF

✅ 설치 끝
  - 매일 07:30 생성, 08:00 발송, 5분마다 웹 수집 요청 처리
  - 지금 바로 시험:  cd $DIR && .venv/bin/python main.py --dry-run --no-dedupe
  - 로그:            $DIR/data/launchd.log, $DIR/data/queue.log
  - 등록 확인:       launchctl list | grep hbuzz
  - 전원 어댑터를 꽂아 두세요.
EOF
