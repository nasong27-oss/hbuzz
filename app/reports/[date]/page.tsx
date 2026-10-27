import Link from "next/link";
import { notFound } from "next/navigation";
import { resendReport } from "@/app/actions";
import { currentRole } from "@/lib/auth";
import { ITEM_COLS, q, q1, type Item, type Recipient, type ReportRow } from "@/lib/db";
import { mailConfigured } from "@/lib/mail";
import { CATEGORIES, SENTIMENTS } from "@/lib/search";
import { dayLabel, fmtDateTime, isYmd, kstAddDays } from "@/lib/time";
import { Flash, IssueSummary, ItemList, Nav, SendPanel } from "@/components/ui";

export const dynamic = "force-dynamic";

type SP = Record<string, string | string[] | undefined>;

export default async function ReportPage({
  params,
  searchParams,
}: {
  params: Promise<{ date: string }>;
  searchParams: Promise<SP>;
}) {
  const { date } = await params;
  const sp = await searchParams;
  if (!isYmd(date)) notFound();
  const role = await currentRole();
  const isAdmin = role === "admin";

  const report = await q1<ReportRow & { html: string }>(
    `SELECT id, report_date, subject, issue_summary, stats, errors, keywords, created_at, html
     FROM reports WHERE report_date=$1`,
    [date],
  );
  if (!report) notFound();

  const many = (v: string | string[] | undefined) => (Array.isArray(v) ? v : v ? [v] : []);
  const sel = { s: many(sp.s), c: many(sp.c) };

  const [items, news, sends, recipients, neighbors] = await Promise.all([
    q<Item>(
      `SELECT ${ITEM_COLS} FROM items WHERE report_date=$1
       ORDER BY CASE sentiment WHEN '부정' THEN 0 WHEN '중립' THEN 1 ELSE 2 END, post_date DESC NULLS LAST`,
      [date],
    ),
    q<{ id: number; title: string; link: string; press: string }>(
      `SELECT id, title, link, press FROM news WHERE report_date=$1 ORDER BY id`,
      [date],
    ),
    q<{ id: number; kind: string; status: string; recipients: string[]; created_at: string; error: string | null }>(
      `SELECT id, kind, status, recipients, created_at, error FROM send_logs
       WHERE report_date=$1 ORDER BY created_at DESC`,
      [date],
    ),
    q<Recipient>(`SELECT * FROM recipients ORDER BY name, email`),
    q<{ report_date: string }>(
      `SELECT report_date FROM reports WHERE report_date IN ($1, $2)`,
      [kstAddDays(date, -1), kstAddDays(date, 1)],
    ),
  ]);
  const has = new Set(neighbors.map((n) => n.report_date));
  const prev = kstAddDays(date, -1);
  const next = kstAddDays(date, 1);

  const shown = items.filter(
    (it) => (!sel.s.length || sel.s.includes(it.sentiment)) && (!sel.c.length || sel.c.includes(it.category)),
  );
  const st = report.stats as Record<string, unknown>;
  const errors = Object.entries(report.errors ?? {});
  const fallbackCount = items.filter((i) => i.fallback).length;

  return (
    <>
      <Nav active="home" isAdmin={isAdmin} />
      <main className="wrap page">
        <Flash sp={sp} />
        <div className="row" style={{ justifyContent: "space-between" }}>
          <Link href="/" className="btn-quiet small">
            ← 리포트 목록
          </Link>
          <div className="row">
            {has.has(prev) && (
              <Link className="btn" href={`/reports/${prev}`}>
                전날
              </Link>
            )}
            {has.has(next) && (
              <Link className="btn" href={`/reports/${next}`}>
                다음날
              </Link>
            )}
          </div>
        </div>

        <section style={{ marginTop: 16 }}>
          <h1>{dayLabel(date)} 리포트</h1>
          <p className="lede">
            소비자여론 {String(st.consumer ?? "–")}건, 부정 {String(st.negative ?? "–")}건, 앱 관련{" "}
            {String(st.app ?? "–")}건. {fmtDateTime(report.created_at)} 생성
            {st.model ? `, 분류 모델 ${String(st.model)}` : ""}.
          </p>
          {fallbackCount > 0 && (
            <div className="flash warn">
              {fallbackCount}건은 AI 분류 실패로 키워드 규칙이 적용됐습니다. 감성 판단이 덜 정확할 수 있습니다.
            </div>
          )}
          <div className="panel">
            <IssueSummary summary={report.issue_summary} />
          </div>
        </section>

        <section>
          <h2>게시글 {shown.length}건</h2>
          <form className="row" style={{ marginBottom: 12 }} method="get">
            <div className="checks">
              {SENTIMENTS.map((s) => (
                <label key={s}>
                  <input type="checkbox" name="s" value={s} defaultChecked={sel.s.includes(s)} />
                  {s}
                </label>
              ))}
              <span className="muted">|</span>
              {CATEGORIES.map((c) => (
                <label key={c}>
                  <input type="checkbox" name="c" value={c} defaultChecked={sel.c.includes(c)} />
                  {c}
                </label>
              ))}
            </div>
            <button type="submit">필터 적용</button>
            {(sel.s.length > 0 || sel.c.length > 0) && (
              <Link href={`/reports/${date}`} className="btn-quiet small">
                필터 해제
              </Link>
            )}
          </form>
          <ItemList items={shown} empty="조건에 맞는 게시글이 없습니다." />
        </section>

        {news.length > 0 && (
          <section>
            <h2>함께 본 업계 뉴스</h2>
            <ul className="items">
              {news.map((n) => (
                <li key={n.id} className="item">
                  <a className="item-title" href={n.link} target="_blank" rel="noreferrer noopener">
                    {n.title}
                  </a>
                  <div className="item-meta">{n.press}</div>
                </li>
              ))}
            </ul>
          </section>
        )}

        {errors.length > 0 && (
          <section>
            <h2>수집 문제</h2>
            <div className="table-wrap">
              <table className="t">
                <tbody>
                  {errors.map(([k, v]) => (
                    <tr key={k}>
                      <td style={{ whiteSpace: "nowrap", fontWeight: 600 }}>{k}</td>
                      <td>{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        <section>
          <details>
            <summary>받은 메일 그대로 보기</summary>
            <iframe
              className="preview"
              title="메일 미리보기"
              sandbox=""
              srcDoc={report.html}
              style={{ marginTop: 12 }}
            />
          </details>
        </section>

        <section>
          <h2>이 리포트 발송 기록</h2>
          {sends.length ? (
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr>
                    <th>시각</th>
                    <th>구분</th>
                    <th>받는 사람</th>
                    <th>결과</th>
                  </tr>
                </thead>
                <tbody>
                  {sends.map((s) => (
                    <tr key={s.id}>
                      <td style={{ whiteSpace: "nowrap" }}>{fmtDateTime(s.created_at)}</td>
                      <td>{s.kind === "daily" ? "아침 자동" : "다시 보내기"}</td>
                      <td className="small">{s.recipients.length}명</td>
                      <td>
                        <span className={`status ${s.status}`}>{s.status === "success" ? "성공" : "실패"}</span>
                        {s.error && <div className="small muted">{s.error}</div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">발송 기록이 없습니다.</p>
          )}
        </section>

        <section>
          <SendPanel
            recipients={recipients}
            isAdmin={isAdmin}
            action={resendReport}
            hidden={{ date }}
            count={items.length}
            desc="아침에 나간 리포트 메일을 그대로 다시 보냅니다."
            disabledReason={mailConfigured() ? null : "발송 설정(GMAIL_APP_PASSWORD)이 아직 없습니다."}
          />
        </section>
      </main>
    </>
  );
}
