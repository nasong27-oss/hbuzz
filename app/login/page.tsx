import { login } from "@/app/actions";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const next = typeof sp.next === "string" ? sp.next : "/";
  const needAdmin = sp.need === "admin";
  return (
    <main className="wrap">
      <form action={login} className="login panel stack">
        <div>
          <h1 style={{ fontSize: 22 }}>현대카드 버즈</h1>
          <p className="muted small" style={{ margin: 0 }}>
            {needAdmin ? "관리 화면은 관리자 비밀번호가 필요합니다." : "팀 비밀번호를 입력하세요."}
          </p>
        </div>
        {sp.err && (
          <div className="flash err" role="alert" style={{ margin: 0 }}>
            비밀번호가 맞지 않습니다.
          </div>
        )}
        <input type="hidden" name="next" value={next} />
        <div className="field">
          <label htmlFor="password">비밀번호</label>
          <input id="password" name="password" type="password" autoComplete="current-password" required autoFocus />
        </div>
        <button className="btn-primary" type="submit">
          들어가기
        </button>
      </form>
    </main>
  );
}
