"""설정 로더 — .env 파일을 읽어 환경변수로 노출한다."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORT_DIR = os.path.join(BASE_DIR, "reports")


def load_env():
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


load_env()

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
# 분류기: Anthropic Claude (본인 API 키). 모델은 CLAUDE_MODEL 로 교체 가능.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
# Neon Postgres — 웹 대시보드와 공유하는 DB. 비어 있으면 DB 연동을 건너뛴다(로컬 전용 동작).
DATABASE_URL = os.environ.get("DATABASE_URL", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
# X(트위터) 보조 수집기용 로그인 계정. 실패해도 데일리 실행은 절대 막지 않는다(베스트에포트).
X_ACCOUNT = os.environ.get("X_ACCOUNT", "")
X_PASSWORD = os.environ.get("X_PASSWORD", "")
# 로그인 챌린지(의심 로그인 등) 대응용 @handle. 비어 있어도 우선 시도한다.
X_USERNAME = os.environ.get("X_USERNAME", "")
# 발신 Gmail. 기본값 nasong27@gmail.com (앱 비밀번호는 .env 에만).
GMAIL_USER = os.environ.get("GMAIL_USER", "") or "nasong27@gmail.com"
REPORT_TO = [x.strip() for x in os.environ.get("REPORT_TO", "").split(",") if x.strip()]
KEYWORDS = [x.strip() for x in os.environ.get("KEYWORDS", "현대카드").split(",") if x.strip()]

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
