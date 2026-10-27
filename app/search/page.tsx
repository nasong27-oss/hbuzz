import Link from "next/link";
import { requestCollect, sendSearch } from "@/app/actions";
import { currentRole } from "@/lib/auth";
import { dbConfigured, q, type Recipient } from "@/lib/db";
import { mailConfigured } from "@/lib/mail";
import { CATEGORIES, parseSearch, searchItems, searchToQuery, SENTIMENTS } from "@/lib/search";
import { fmtDateTime, kstAddDays, kstToday } from "@/lib/time";
import { Flash, ItemList, Nav, SendPanel } from "@/components/ui";
import { SetupNotice } from "@/components/setup";

export const dynamic = "force-dynamic";

type SP = Record<string, string | string[] | undefined>;

type QReq = {
  id: number;
  keywords: string[];
  date_from: string;
  date_to: string;
  status: string;
  result_count: number;
  error: string | null;
  created_at: string;
};

const STATUS_LABEL: Record<string, string> = {
  pending: "대기 중",
  running: "수집 중",
  done: "완료",
  failed: "실패",
};

export default async function SearchPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const role = await currentRole();
  const isAdmin = role === "admin";
  const p = parseSearch(sp);
  const today = kstToday();

  if (!dbConfigured()) {
    return (
      <>
        <Nav active="search" isAdmin={isAdmin} />
        <main className="wrap page">
          <SetupNotice />
        </main>
      </>
    );
  }

  const [items, requests, recipients] = await Promise.all([
    searchItems(p),
    q<QReq>(
      `SELECT id, keywords, date_from, date_to, status, result_count, error, created_at
       FROM query_requests ORDER BY created_at DESC LIMIT 8`,
    ),
    q<Recipient>(`SELECT * FROM recipients ORDER BY name, email`),
  ]);
  const activeReq = p.queryId ? requests.find((r) => r.id === p.queryId) : null;
  const presets: [string, number][] = [
    ["오늘", 0],
    ["최근 7일", 6],
    ["최근 30일", 29],
  ];
  const presetHref = (days: number) =>
    `/search?${searchToQuery({ ...p, from: kstAddDays(today, -days), to: today, queryId: null })}`;

  const hidden: Record<string, string | string[]> = {
    kw: p.keywords.join(", "),
    from: p.from,
    to: p.to,
    s: p.sentiment,
    c: p.category,
  };
  if (p.appOnly) hidden.app = "1";
  if (p.queryId) hidden.qid = String(p.queryId);

  return (
    <>
      <Nav active="search" isAdmin={isAdmin} />
      <main className="wrap page">
        <Flash sp={sp} />
        <h1>조회·발송</h1>
        <p className="lede">
          키워드와 기간으로 쌓인 게시글을 찾고, 결과를 바로 메일로 보냅니다. 아직 모이지 않은 키워드는 새로 수집을
          요청하세요.
        </p>

        <form method="get" className="panel stack">
          <div className="form-grid">
            <div className="field wide">
              <label htmlFor="kw">키워드 (쉼표로 여러 개, 비우면 전체)</label>
              <input id="kw" name="kw" type="text" defaultValue={p.keywords.join(", ")} placeholder="헨리, 애플페이, 로그인" />
            </div>
            <div className="field">
              <label htmlFor="from">시작일</label>
              <input id="from" name="from" type="date" defaultValue={p.from} max={today} />
            </div>
            <div className="field">
              <label htmlFor="to">종료일</label>
              <input id="to" name="to" type="date" defaultValue={p.to} max={today} />
            </div>
          </div>
          <div className="row">
            {presets.map(([label, days]) => (
              <Link key={label} href={presetHref(days)} className="btn small">
                {label}
              </Link>
            ))}
          </div>
          <div className="checks">
            {SENTIMENTS.map((s) => (
              <label key={s}>
                <input type="checkbox" name="s" value={s} defaultChecked={p.sentiment.includes(s)} />
                {s}
              </label>
            ))}
            <span className="muted">|</span>
            {CATEGORIES.map((c) => (
              <label key={c}>
                <input type="checkbox" name="c" value={c} defaultChecked={p.category.includes(c)} />
                {c}
              </label>
            ))}
            <span className="muted">|</span>
            <label>
              <input type="checkbox" name="app" value="1" defaultChecked={p.appOnly} />앱 관련만
            </label>
          </div>
          <div className="row end">
            <button type="submit" formAction={requestCollect} title="집 Mac이 커뮤니티를 새로 검색합니다">
              새로 수집 요청
            </button>
            <button className="btn-primary" type="submit">
              조회
            </button>
          </div>
        </form>

        <section>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
            <h2 style={{ margin: 0 }}>
              {activeReq ? `수집 요청 #${activeReq.id} 결과` : "결과"} {items.length}건
              {items.length >= 300 && <span className="muted small"> (최대 300건 표시)</span>}
            </h2>
            {activeReq && (
              <Link href={`/search?${searchToQuery({ ...p, queryId: null })}`} className="btn-quiet small">
                전체 아카이브에서 보기
              </Link>
            )}
          </div>
          <ItemList
            items={items}
            empty={
              p.keywords.length
                ? "이 조건으로 쌓인 게시글이 없습니다. 기간을 넓히거나, 새로 수집을 요청해 보세요."
                : "이 기간에 쌓인 게시글이 없습니다."
            }
          />
        </section>

        {items.length > 0 && (
          <section>
            <SendPanel
              recipients={recipients}
              isAdmin={isAdmin}
              action={sendSearch}
              hidden={hidden}
              count={items.length}
              disabledReason={mailConfigured() ? null : "발송 설정(GMAIL_APP_PASSWORD)이 아직 없습니다."}
            />
          </section>
        )}

        <section>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
            <h2 style={{ margin: 0 }}>최근 수집 요청</h2>
            <Link href={`/search?${searchToQuery(p)}`} className="btn-quiet small">
              새로고침
            </Link>
          </div>
          {requests.length ? (
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr>
                    <th>요청</th>
                    <th>키워드</th>
                    <th>기간</th>
                    <th>상태</th>
                    <th className="num">결과</th>
                  </tr>
                </thead>
                <tbody>
                  {requests.map((r) => (
                    <tr key={r.id}>
                      <td style={{ whiteSpace: "nowrap" }}>
                        #{r.id}
                        <div className="small muted">{fmtDateTime(r.created_at)}</div>
                      </td>
                      <td>{r.keywords.join(", ")}</td>
                      <td className="small" style={{ whiteSpace: "nowrap" }}>
                        {r.date_from} ~ {r.date_to}
                      </td>
                      <td>
                        <span className={`status ${r.status}`}>{STATUS_LABEL[r.status] ?? r.status}</span>
                        {r.error && <div className="small muted">{r.error}</div>}
                      </td>
                      <td className="num">
                        {r.status === "done" ? (
                          <Link
                            href={`/search?${searchToQuery({
                              keywords: r.keywords,
                              from: r.date_from,
                              to: r.date_to,
                              sentiment: [],
                              category: [],
                              appOnly: false,
                              queryId: r.id,
                            })}`}
                          >
                            {r.result_count}건 보기
                          </Link>
                        ) : (
                          "–"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">아직 요청이 없습니다.</p>
          )}
          <p className="small muted">
            새로 수집은 집 Mac이 &lsquo;현대카드 + 키워드&rsquo;로 커뮤니티를 검색해 Claude로 분류합니다. 보통 5~15분
            걸리고, Mac이 꺼져 있으면 켜질 때 처리됩니다.
          </p>
        </section>
      </main>
    </>
  );
}
