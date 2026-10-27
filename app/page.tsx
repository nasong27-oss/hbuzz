import Link from "next/link";
import { currentRole } from "@/lib/auth";
import { dbConfigured, ITEM_COLS, q, type Item, type ReportRow } from "@/lib/db";
import { healthWarnings } from "@/lib/health";
import { dayLabel, kstAddDays, kstToday } from "@/lib/time";
import { Flash, IssueSummary, ItemList, Nav, SentimentStrip, type StripDay } from "@/components/ui";
import { SetupNotice } from "@/components/setup";

export const dynamic = "force-dynamic";

export default async function Dashboard({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const role = await currentRole();
  const isAdmin = role === "admin";

  let body: React.ReactNode;
  try {
    body = dbConfigured() ? await Content() : <SetupNotice />;
  } catch (e) {
    body = <SetupNotice error={e instanceof Error ? e.message : String(e)} />;
  }

  return (
    <>
      <Nav active="home" isAdmin={isAdmin} />
      <main className="wrap page">
        <Flash sp={sp} />
        {body}
      </main>
    </>
  );
}

async function Content() {
  const today = kstToday();
  const from = kstAddDays(today, -6);

  const [reports, counts, sends, warnings] = await Promise.all([
    q<ReportRow>(
      `SELECT id, report_date, subject, issue_summary, stats, errors, keywords, created_at
       FROM reports WHERE report_date BETWEEN $1 AND $2 ORDER BY report_date DESC`,
      [from, today],
    ),
    q<{ d: string; sentiment: string; n: number }>(
      `SELECT report_date AS d, sentiment, count(*)::int AS n FROM items
       WHERE report_date BETWEEN $1 AND $2 AND type='소비자여론'
       GROUP BY 1, 2`,
      [from, today],
    ),
    q<{ d: string; n: number; rcpt: number }>(
      `SELECT report_date AS d, count(*)::int AS n, max(cardinality(recipients))::int AS rcpt
       FROM send_logs WHERE kind='daily' AND status='success' AND report_date BETWEEN $1 AND $2 GROUP BY 1`,
      [from, today],
    ),
    healthWarnings(),
  ]);

  const byDate = new Map(reports.map((r) => [r.report_date, r]));
  const days: StripDay[] = [];
  for (let i = 6; i >= 0; i--) {
    const d = kstAddDays(today, -i);
    const c = (s: string) => counts.find((x) => x.d === d && x.sentiment === s)?.n ?? 0;
    days.push({ date: d, neg: c("부정"), neu: c("중립"), pos: c("긍정"), hasReport: byDate.has(d) });
  }
  const sentBy = new Map(sends.map((s) => [s.d, s]));
  const latest = reports[0];
  const negatives = latest
    ? await q<Item>(
        `SELECT ${ITEM_COLS} FROM items WHERE report_date=$1 AND type='소비자여론' AND sentiment='부정'
         ORDER BY post_date DESC NULLS LAST LIMIT 5`,
        [latest.report_date],
      )
    : [];

  return (
    <>
      {warnings.map((w, i) => (
        <div key={i} className={`flash ${w.level === "err" ? "err" : "warn"}`} role="status">
          {w.text}
        </div>
      ))}

      <section>
        <h1>최근 7일 여론</h1>
        <p className="lede">매일 아침 커뮤니티와 앱 리뷰에서 모은 현대카드 소비자여론입니다.</p>
        <SentimentStrip days={days} current={latest?.report_date} />
      </section>

      {latest ? (
        <section>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
            <h2 style={{ margin: 0 }}>{dayLabel(latest.report_date)} 주요 이슈</h2>
            <Link href={`/reports/${latest.report_date}`} className="btn">
              리포트 전체 보기
            </Link>
          </div>
          <div className="panel">
            <IssueSummary summary={latest.issue_summary} />
          </div>
          {negatives.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <h3>부정 여론</h3>
              <ItemList items={negatives} empty={null} />
            </div>
          )}
        </section>
      ) : (
        <section>
          <div className="empty-state">
            최근 7일 리포트가 없습니다. 집 Mac에서 매일 07:30에 리포트가 만들어지면 여기에 나타납니다.
          </div>
        </section>
      )}

      <section>
        <h2>발송 내역</h2>
        {reports.length ? (
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th>날짜</th>
                  <th className="hide-sm">제목</th>
                  <th className="num">소비자여론</th>
                  <th className="num">부정</th>
                  <th>아침 발송</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((r) => {
                  const s = sentBy.get(r.report_date);
                  const st = r.stats as { consumer?: number; negative?: number };
                  return (
                    <tr key={r.id}>
                      <td style={{ whiteSpace: "nowrap" }}>
                        <Link href={`/reports/${r.report_date}`}>{dayLabel(r.report_date)}</Link>
                      </td>
                      <td className="hide-sm">{r.subject}</td>
                      <td className="num">{st.consumer ?? "–"}</td>
                      <td className="num sent-neg">{st.negative ?? "–"}</td>
                      <td>
                        {s ? (
                          <span className="status success">발송됨 ({s.rcpt}명)</span>
                        ) : (
                          <span className="status failed">기록 없음</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">기록이 없습니다.</p>
        )}
      </section>
    </>
  );
}
