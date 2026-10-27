/** DB 미연결/스키마 미적용 시 안내 */
export function SetupNotice({ error }: { error?: string }) {
  return (
    <div className="panel stack">
      <h2>데이터베이스 연결이 필요합니다</h2>
      <p className="small" style={{ margin: 0 }}>
        Vercel 프로젝트 &gt; Storage에서 Neon 데이터베이스를 연결하면 <code>DATABASE_URL</code>이 자동으로 추가됩니다.
        연결 후 관리 &gt; 운영에서 <b>DB 초기화</b>를 한 번 누르세요.
      </p>
      {error && <p className="small muted" style={{ margin: 0 }}>오류: {error}</p>}
    </div>
  );
}
