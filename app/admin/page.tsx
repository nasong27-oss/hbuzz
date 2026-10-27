import Link from "next/link";
import { requireRole } from "@/lib/auth";
import { dbConfigured, q, type Recipient } from "@/lib/db";
import { healthWarnings } from "@/lib/health";
import { mailConfigured, SENDER } from "@/lib/mail";
import { fmtDateTime } from "@/lib/time";
import { Flash, Nav } from "@/components/ui";
import { CloudPanel } from "@/components/CloudPanel";
import { kstToday } from "@/lib/time";
import { alertRecipients } from "@/lib/alert";
import {
  addKeywords,
  addRecipients,
  deleteKeyword,
  deleteRecipient,
  initDb,
  renameRecipient,
  sendTestAlert,
  sendTestMail,
  toggleKeyword,
  toggleRecipient,
} from "./actions";

export const dynamic = "force-dynamic";

type SP = Record<string, string | string[] | undefined>;
const TABS = [
  ["recipients", "수신자"],
  ["keywords", "키워드"],
  ["sends", "발송 이력"],
  ["ops", "운영"],
] as const;
type Tab = (typeof TABS)[number][0];

export default async function AdminPage({ searchParams }: { searchParams: Promise<SP> }) {
  await requireRole("admin");
  const sp = await searchParams;
  const tab: Tab = (TABS.map((t) => t[0]) as string[]).includes(String(sp.tab)) ? (sp.tab as Tab) : "recipients";

  let body: React.ReactNode;
  try {
    if (!dbConfigured()) throw new Error("DATABASE_URL 이 설정되지 않았습니다.");
    body =
      tab === "recipients" ? (
        await Recipients()
      ) : tab === "keywords" ? (
        await Keywords()
      ) : tab === "sends" ? (
        await Sends()
      ) : (
        await Ops()
      );
  } catch (e) {
    // 스키마 미적용 상태여도 운영 탭에서 초기화할 수 있게
    body = <OpsSetup error={e instanceof Error ? e.message : String(e)} />;
  }

  return (
    <>
      <Nav active="admin" isAdmin />
      <main className="wrap page">
        <Flash sp={sp} />
        <h1>관리</h1>
        <nav className="tabs" aria-label="관리 메뉴">
          {TABS.map(([k, label]) => (
            <Link key={k} href={`/admin?tab=${k}`} aria-current={k === tab ? "page" : undefined}>
              {label}
            </Link>
          ))}
        </nav>
        {body}
      </main>
    </>
  );
}

function OpsSetup({ error }: { error: string }) {
  return (
    <div className="panel stack">
      <h2>DB 준비가 필요합니다</h2>
      <p className="small" style={{ margin: 0 }}>
        Neon을 연결했다면 아래 버튼으로 테이블을 만드세요. 여러 번 눌러도 기존 데이터는 지워지지 않습니다.
      </p>
      <p className="small muted" style={{ margin: 0 }}>오류: {error}</p>
      {dbConfigured() && (
        <form action={initDb}>
          <button className="btn-primary" type="submit">DB 초기화</button>
        </form>
      )}
    </div>
  );
}

// ── 수신자 ─────────────────────────────────────────

async function Recipients() {
  const rows = await q<Recipient>(`SELECT * FROM recipients ORDER BY active DESC, name, email`);
  const daily = rows.filter((r) => r.active && r.receive_daily).length;
  return (
    <div className="stack">
      <p className="lede" style={{ marginBottom: 0 }}>
        아침 리포트는 <b>사용</b>과 <b>아침 메일</b>이 모두 켜진 {daily}명에게 갑니다. 목록이 비어 있으면 Mac의 .env
        REPORT_TO로 보냅니다.
      </p>
      <form action={addRecipients} className="panel stack">
        <h2 style={{ margin: 0 }}>수신자 추가</h2>
        <div className="form-grid">
          <div className="field wide">
            <label htmlFor="emails">이메일 (쉼표·줄바꿈으로 여러 명)</label>
            <textarea id="emails" name="emails" rows={2} required placeholder="kim@hyundaicard.com, lee@hyundaicard.com" />
          </div>
          <div className="field">
            <label htmlFor="name">이름 (한 명일 때)</label>
            <input id="name" name="name" type="text" />
          </div>
          <div className="field" style={{ justifyContent: "flex-end" }}>
            <label style={{ fontWeight: 500, display: "flex", gap: 6, alignItems: "center", paddingBottom: 10 }}>
              <input type="checkbox" name="daily" value="1" defaultChecked />
              아침 메일 받기
            </label>
          </div>
        </div>
        <div className="row end">
          <button className="btn-primary" type="submit">추가</button>
        </div>
      </form>

      {rows.length ? (
        <div className="table-wrap">
          <table className="t">
            <thead>
              <tr>
                <th>이메일</th>
                <th>이름</th>
                <th>사용</th>
                <th>아침 메일</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} style={r.active ? undefined : { opacity: 0.55 }}>
                  <td>{r.email}</td>
                  <td>
                    <form action={renameRecipient} className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                      <input type="hidden" name="id" value={r.id} />
                      <input
                        type="text"
                        name="name"
                        defaultValue={r.name}
                        aria-label={`${r.email} 이름`}
                        style={{ minWidth: 90, padding: "5px 8px" }}
                      />
                      <button className="btn-quiet small" type="submit">저장</button>
                    </form>
                  </td>
                  <td>
                    <form action={toggleRecipient}>
                      <input type="hidden" name="id" value={r.id} />
                      <input type="hidden" name="field" value="active" />
                      <button className="btn-quiet small" type="submit" aria-pressed={r.active}>
                        {r.active ? "켜짐" : "꺼짐"}
                      </button>
                    </form>
                  </td>
                  <td>
                    <form action={toggleRecipient}>
                      <input type="hidden" name="id" value={r.id} />
                      <input type="hidden" name="field" value="receive_daily" />
                      <button className="btn-quiet small" type="submit" aria-pressed={r.receive_daily}>
                        {r.receive_daily ? "받음" : "안 받음"}
                      </button>
                    </form>
                  </td>
                  <td className="num">
                    <form action={deleteRecipient}>
                      <input type="hidden" name="id" value={r.id} />
                      <button className="btn-quiet small btn-danger" type="submit">삭제</button>
                    </form>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty-state">등록된 수신자가 없습니다. 위에서 추가하세요.</div>
      )}
    </div>
  );
}

// ── 키워드 ─────────────────────────────────────────

async function Keywords() {
  const rows = await q<{ id: number; keyword: string; active: boolean }>(`SELECT * FROM keywords ORDER BY id`);
  return (
    <div className="stack">
      <p className="lede" style={{ marginBottom: 0 }}>
        아침 수집에 쓰는 검색어입니다. 목록이 비어 있으면 Mac의 .env KEYWORDS를 씁니다. 관련성 필터 때문에
        &lsquo;현대카드&rsquo;나 &lsquo;현카&rsquo;가 들어간 글만 남습니다.
      </p>
      <form action={addKeywords} className="panel stack">
        <h2 style={{ margin: 0 }}>키워드 추가</h2>
        <div className="field">
          <label htmlFor="keywords">키워드 (쉼표로 여러 개)</label>
          <input id="keywords" name="keywords" type="text" required placeholder="현대카드, 현카, 현대카드 헨리" />
        </div>
        <div className="row end">
          <button className="btn-primary" type="submit">추가</button>
        </div>
      </form>
      {rows.length ? (
        <div className="table-wrap">
          <table className="t">
            <thead>
              <tr>
                <th>키워드</th>
                <th>사용</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((k) => (
                <tr key={k.id} style={k.active ? undefined : { opacity: 0.55 }}>
                  <td>{k.keyword}</td>
                  <td>
                    <form action={toggleKeyword}>
                      <input type="hidden" name="id" value={k.id} />
                      <button className="btn-quiet small" type="submit" aria-pressed={k.active}>
                        {k.active ? "켜짐" : "꺼짐"}
                      </button>
                    </form>
                  </td>
                  <td className="num">
                    <form action={deleteKeyword}>
                      <input type="hidden" name="id" value={k.id} />
                      <button className="btn-quiet small btn-danger" type="submit">삭제</button>
                    </form>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty-state">DB 키워드가 없어 Mac 설정값을 쓰는 중입니다.</div>
      )}
    </div>
  );
}

// ── 발송 이력 ─────────────────────────────────────────

const KIND: Record<string, string> = {
  daily: "아침 자동",
  backup: "클라우드 백업",
  manual: "조회 결과",
  alert: "실패 알림",
  alert_test: "알림 테스트",
  resend: "리포트 재발송",
};

async function Sends() {
  const rows = await q<{
    id: number;
    kind: string;
    subject: string;
    recipients: string[];
    status: string;
    error: string | null;
    item_count: number;
    report_date: string | null;
    created_at: string;
  }>(`SELECT * FROM send_logs ORDER BY created_at DESC LIMIT 100`);
  if (!rows.length) return <div className="empty-state">발송 기록이 없습니다.</div>;
  return (
    <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th>시각</th>
            <th>구분</th>
            <th>제목</th>
            <th>받는 사람</th>
            <th>결과</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td style={{ whiteSpace: "nowrap" }}>{fmtDateTime(r.created_at)}</td>
              <td style={{ whiteSpace: "nowrap" }}>{KIND[r.kind] ?? r.kind}</td>
              <td>
                {r.report_date ? <Link href={`/reports/${r.report_date}`}>{r.subject}</Link> : r.subject}
              </td>
              <td className="small">
                <details>
                  <summary>{r.recipients.length}명</summary>
                  {r.recipients.join(", ")}
                </details>
              </td>
              <td>
                <span className={`status ${r.status}`}>{r.status === "success" ? "성공" : "실패"}</span>
                {r.error && <div className="small muted">{r.error}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── 운영 ─────────────────────────────────────────

type Run = {
  id: number;
  kind: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  sources: Record<string, { count: number; secs: number; tries: number; error?: string }>;
  claude: {
    calls?: number;
    fallback?: number;
    input_tokens?: number;
    output_tokens?: number;
    errors?: string[];
    via?: { "claude-code"?: number; api?: number };
    cc_cost_usd?: number;
  };
  summary: Record<string, unknown>;
  error: string | null;
};

const RUN_KIND: Record<string, string> = {
  generate: "리포트 생성",
  send: "아침 발송",
  query: "수집 요청",
  cloud: "클라우드 백업",
};

type DiagResult = { status: string; detail: string; count: number; secs: number; http: number[] };
type DiagRow = { env: string; ran_at: string; results: { results: Record<string, DiagResult> } };

const ENV_LABEL: Record<string, string> = {
  "home-mac": "집 Mac",
  "vercel-icn1": "Vercel 서울",
  "github-actions": "GitHub Actions",
};
const DIAG_LABEL: Record<string, [string, string]> = {
  ok: ["수집됨", "success"],
  empty: ["0건", "pending"],
  unknown: ["판단불가", "pending"],
  blocked: ["차단", "failed"],
  unreachable: ["접속불가", "failed"],
  error: ["오류", "failed"],
  timeout: ["시간초과", "failed"],
};

function DiagTable({ rows }: { rows: DiagRow[] }) {
  if (!rows.length)
    return (
      <div className="empty-state">
        아직 진단 기록이 없습니다. 위의 &lsquo;클라우드 진단 실행&rsquo;을 누르면 Vercel 서울에서 사이트별 수집 가능 여부를
        확인합니다.
      </div>
    );
  const sites = [...new Set(rows.flatMap((r) => Object.keys(r.results.results ?? {})))].sort();
  return (
    <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th>사이트</th>
            {rows.map((r) => (
              <th key={r.env}>
                {ENV_LABEL[r.env] ?? r.env}
                <div className="small muted" style={{ fontWeight: 400 }}>
                  {fmtDateTime(r.ran_at)}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sites.map((site) => (
            <tr key={site}>
              <td style={{ whiteSpace: "nowrap", fontWeight: 600 }}>{site}</td>
              {rows.map((r) => {
                const d = r.results.results?.[site];
                if (!d) return <td key={r.env} className="muted">–</td>;
                const [label, cls] = DIAG_LABEL[d.status] ?? [d.status, "pending"];
                return (
                  <td key={r.env} title={d.detail}>
                    <span className={`status ${cls}`}>{label}</span>
                    <span className="small muted">
                      {" "}
                      {d.count}건, {d.secs}초
                    </span>
                    {d.status !== "ok" && d.detail && (
                      <div className="small muted" style={{ maxWidth: "36ch" }}>
                        {d.detail.slice(0, 80)}
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function minutes(a: string, b: string | null) {
  if (!b) return "진행 중";
  const m = (new Date(b).getTime() - new Date(a).getTime()) / 60000;
  return m < 1 ? `${Math.round(m * 60)}초` : `${m.toFixed(1)}분`;
}

async function Ops() {
  const [runs, warnings, diags, primary, lastAlert] = await Promise.all([
    q<Run>(`SELECT * FROM runs ORDER BY started_at DESC LIMIT 20`),
    healthWarnings(),
    q<DiagRow>(
      `SELECT DISTINCT ON (env) env, ran_at, results FROM diagnostics
       ORDER BY env, ran_at DESC`,
    ),
    q<{ n: number }>(
      `SELECT count(*)::int AS n FROM reports WHERE report_date = $1
       AND COALESCE((stats->>'backup')::boolean, false) = false`,
      [kstToday()],
    ),
    q<{ created_at: string; subject: string }>(
      `SELECT created_at, subject FROM send_logs WHERE kind IN ('alert','alert_test')
       ORDER BY created_at DESC LIMIT 1`,
    ),
  ]);
  const envOrder = ["home-mac", "vercel-icn1", "github-actions"];
  diags.sort((a, b) => envOrder.indexOf(a.env) - envOrder.indexOf(b.env));
  const checks: [string, boolean, string][] = [
    ["데이터베이스", dbConfigured(), "DATABASE_URL"],
    ["메일 발송", mailConfigured(), `GMAIL_APP_PASSWORD (발신 ${SENDER})`],
    ["팀 비밀번호", Boolean(process.env.SITE_PASSWORD), "SITE_PASSWORD"],
  ];
  return (
    <div className="stack">
      {warnings.length ? (
        warnings.map((w, i) => (
          <div key={i} className={`flash ${w.level === "err" ? "err" : "warn"}`}>
            {w.text}
          </div>
        ))
      ) : (
        <div className="flash ok">
          지금 확인된 문제가 없습니다.
        </div>
      )}

      <div className="cols">
        <div className="panel stack">
          <h2 style={{ margin: 0 }}>웹 설정</h2>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {checks.map(([label, ok, env]) => (
              <li key={label}>
                <span className={`status ${ok ? "success" : "failed"}`}>{ok ? "설정됨" : "없음"}</span> {label}{" "}
                <span className="small muted">{env}</span>
              </li>
            ))}
          </ul>
          <div className="row">
            <form action={sendTestMail}>
              <button type="submit" disabled={!mailConfigured()}>
                테스트 메일 보내기
              </button>
            </form>
            <form action={initDb}>
              <button type="submit">DB 초기화</button>
            </form>
          </div>
        </div>
        <div className="panel stack">
          <h2 style={{ margin: 0 }}>집 Mac 일정</h2>
          <ul style={{ margin: 0, paddingLeft: 18 }} className="small">
            <li>07:30 리포트 생성 (수집, Claude 분류: 구독 우선, 실패 시 API 키)</li>
            <li>08:00 아침 메일 발송</li>
            <li>5분마다 웹 수집 요청 처리</li>
          </ul>
          <p className="small muted" style={{ margin: 0 }}>
            각 작업 전에 GitHub에서 최신 코드를 받아 반영합니다.
          </p>
        </div>
      </div>

      <div className="panel stack">
        <div>
          <h2 style={{ marginBottom: 4 }}>집 Mac 실패 알림</h2>
          <p className="small muted" style={{ margin: 0, maxWidth: "70ch" }}>
            매일 08:10 전후로 아침 리포트 발송 기록을 확인하고, 없으면 원인(Mac 꺼짐, 생성 실패, 발송 실패)과 함께
            메일로 알립니다. 하루 한 번만 보냅니다.
          </p>
        </div>
        <ul style={{ margin: 0, paddingLeft: 18 }} className="small">
          <li>
            받는 주소: <b>{alertRecipients().join(", ")}</b>{" "}
            <span className="muted">(ALERT_TO로 변경)</span>
          </li>
          <li>
            <span className={`status ${process.env.CRON_SECRET ? "success" : "failed"}`}>
              {process.env.CRON_SECRET ? "설정됨" : "없음"}
            </span>{" "}
            점검 비밀키 <span className="muted">CRON_SECRET</span>
          </li>
          <li>
            마지막 알림:{" "}
            {lastAlert[0] ? `${fmtDateTime(lastAlert[0].created_at)} ${lastAlert[0].subject}` : "없음"}
          </li>
        </ul>
        <form action={sendTestAlert}>
          <button type="submit" disabled={!mailConfigured()}>
            테스트 알림 보내기
          </button>
        </form>
      </div>

      <CloudPanel hasPrimaryToday={(primary[0]?.n ?? 0) > 0} hasDiag={diags.some((d) => d.env === "vercel-icn1")} />

      <h2 style={{ marginTop: 12 }}>실행 환경별 수집 진단</h2>
      <DiagTable rows={diags} />

      <h2 style={{ marginTop: 12 }}>최근 실행</h2>
      {runs.length ? (
        <div className="table-wrap">
          <table className="t">
            <thead>
              <tr>
                <th>시작</th>
                <th>작업</th>
                <th>결과</th>
                <th>소요</th>
                <th>상세</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const srcs = Object.entries(r.sources ?? {});
                const failed = srcs.filter(([, s]) => s.error || s.count === 0).length;
                return (
                  <tr key={r.id}>
                    <td style={{ whiteSpace: "nowrap" }}>{fmtDateTime(r.started_at)}</td>
                    <td style={{ whiteSpace: "nowrap" }}>{RUN_KIND[r.kind] ?? r.kind}</td>
                    <td>
                      <span className={`status ${r.status}`}>
                        {r.status === "success" ? "성공" : r.status === "failed" ? "실패" : "진행 중"}
                      </span>
                      {r.error && <div className="small muted">{r.error}</div>}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>{minutes(r.started_at, r.finished_at)}</td>
                    <td className="small">
                      {srcs.length > 0 || r.claude?.calls ? (
                        <details>
                          <summary>
                            사이트 {srcs.length}곳{failed ? `, 0건 ${failed}곳` : ""}
                            {r.claude?.fallback ? `, 키워드 분류 ${r.claude.fallback}건` : ""}
                          </summary>
                          {r.claude?.calls ? (
                            <p style={{ margin: "8px 0" }}>
                              Claude 호출 {r.claude.calls}회
                              {r.claude.via
                                ? ` (구독 ${r.claude.via["claude-code"] ?? 0}, API 키 ${r.claude.via.api ?? 0})`
                                : ""}
                              , 입력 {r.claude.input_tokens?.toLocaleString()} / 출력{" "}
                              {r.claude.output_tokens?.toLocaleString()} 토큰
                              {r.claude.errors?.length ? ` (오류: ${r.claude.errors.join("; ")})` : ""}
                            </p>
                          ) : null}
                          <table className="t" style={{ fontSize: 13 }}>
                            <tbody>
                              {srcs
                                .sort((a, b) => b[1].secs - a[1].secs)
                                .map(([name, s]) => (
                                  <tr key={name}>
                                    <td>{name}</td>
                                    <td className="num">{s.count}건</td>
                                    <td className="num">{s.secs}초</td>
                                    <td className="num">{s.tries}회</td>
                                    <td className="muted">{s.error}</td>
                                  </tr>
                                ))}
                            </tbody>
                          </table>
                        </details>
                      ) : (
                        <span className="muted">–</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty-state">아직 실행 기록이 없습니다. 집 Mac 설정이 끝나면 다음 날 아침부터 쌓입니다.</div>
      )}
    </div>
  );
}
