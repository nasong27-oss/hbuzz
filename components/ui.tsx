import Link from "next/link";
import type { Item, Recipient, Sentiment } from "@/lib/db";
import { dayLabel } from "@/lib/time";
import { logout } from "@/app/actions";

export function Nav({ active, isAdmin }: { active: "home" | "search" | "admin"; isAdmin: boolean }) {
  const cur = (k: string) => (k === active ? ("page" as const) : undefined);
  return (
    <header className="nav">
      <div className="wrap">
        <Link href="/" className="brand">
          현대카드 <span>버즈</span>
        </Link>
        <nav className="nav-links" aria-label="주 메뉴">
          <Link href="/" aria-current={cur("home")}>리포트</Link>
          <Link href="/search" aria-current={cur("search")}>조회·발송</Link>
          {isAdmin && (
            <Link href="/admin" aria-current={cur("admin")}>관리</Link>
          )}
        </nav>
        <form action={logout}>
          <button className="btn-quiet small" type="submit">로그아웃</button>
        </form>
      </div>
    </header>
  );
}

export function Flash({ sp }: { sp: Record<string, string | string[] | undefined> }) {
  const ok = typeof sp.ok === "string" ? sp.ok : null;
  const err = typeof sp.err === "string" ? sp.err : null;
  if (!ok && !err) return null;
  return (
    <div className={`flash ${err ? "err" : "ok"}`} role="status">
      {err ?? ok}
    </div>
  );
}

const SENT_CLASS: Record<Sentiment, string> = { 부정: "sent-neg", 중립: "sent-neu", 긍정: "sent-pos" };

export function ItemList({ items, empty }: { items: Item[]; empty: React.ReactNode }) {
  if (!items.length) return <div className="empty-state">{empty}</div>;
  return (
    <ul className="items">
      {items.map((it) => (
        <li key={it.id} className={`item s-${it.sentiment}`}>
          <a className="item-title" href={it.link} target="_blank" rel="noreferrer noopener">
            {it.title}
          </a>
          <div className="item-meta">
            <span className={SENT_CLASS[it.sentiment]}>{it.sentiment}</span>
            <span>{it.source}</span>
            <span>{it.post_date ?? "날짜 미상"}</span>
            <span className="tag">{it.category}</span>
            {it.app_related && <span className="tag">앱</span>}
            {it.fallback && (
              <span className="tag warn" title="AI 분류가 실패해 키워드 규칙으로 분류됨">
                키워드 분류
              </span>
            )}
          </div>
          {it.reason && <div className="item-reason">{it.reason}</div>}
        </li>
      ))}
    </ul>
  );
}

export type StripDay = { date: string; neg: number; neu: number; pos: number; hasReport: boolean };

export function SentimentStrip({ days, current }: { days: StripDay[]; current?: string }) {
  const max = Math.max(1, ...days.map((d) => d.neg + d.neu + d.pos));
  return (
    <div>
      <div className="strip">
        {days.map((d) => {
          const total = d.neg + d.neu + d.pos;
          const h = (n: number) => `${(n / max) * 100}%`;
          const body = (
            <>
              <div className={`strip-bar${d.hasReport ? "" : " empty"}`} aria-hidden="true">
                {d.hasReport && (
                  <>
                    <i className="neg" style={{ height: h(d.neg) }} />
                    <i className="neu" style={{ height: h(d.neu) }} />
                    <i className="pos" style={{ height: h(d.pos) }} />
                  </>
                )}
              </div>
              <div className="strip-num">
                {d.hasReport ? total : "–"}
                {d.hasReport && d.neg > 0 && <small>부정 {d.neg}</small>}
              </div>
              <div className="strip-label">{dayLabel(d.date)}</div>
            </>
          );
          const label = d.hasReport
            ? `${dayLabel(d.date)} 리포트: 총 ${total}건, 부정 ${d.neg}, 중립 ${d.neu}, 긍정 ${d.pos}`
            : `${dayLabel(d.date)} 리포트 없음`;
          return d.hasReport ? (
            <Link
              key={d.date}
              href={`/reports/${d.date}`}
              className="strip-day"
              aria-label={label}
              aria-current={d.date === current ? "date" : undefined}
            >
              {body}
            </Link>
          ) : (
            <div key={d.date} className="strip-day" aria-label={label}>
              {body}
            </div>
          );
        })}
      </div>
      <div className="legend">
        <span><b style={{ background: "var(--neg)" }} />부정</span>
        <span><b style={{ background: "var(--neu)", opacity: 0.55 }} />중립</span>
        <span><b style={{ background: "var(--pos)" }} />긍정</span>
        <span>소비자여론 기준, 막대를 누르면 그날 리포트</span>
      </div>
    </div>
  );
}

export function IssueSummary({ summary }: { summary: Partial<Record<Sentiment, string[]>> }) {
  const groups: [Sentiment, string][] = [
    ["부정", "neg"],
    ["중립", "neu"],
    ["긍정", "pos"],
  ];
  return (
    <div className="issues">
      {groups.map(([s, cls]) => (
        <div key={s}>
          <h3>
            <span className={`dot ${cls}`} aria-hidden="true" />
            {s} 이슈
          </h3>
          {summary[s]?.length ? (
            <ul>
              {summary[s]!.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          ) : (
            <p className="muted small" style={{ margin: 0 }}>없음</p>
          )}
        </div>
      ))}
    </div>
  );
}

/** 결과 메일 발송 폼. 일반 사용자는 등록된 수신자만, 관리자는 직접 입력한 주소도 가능. */
export function SendPanel({
  recipients,
  isAdmin,
  action,
  hidden,
  count,
  desc,
  disabledReason,
}: {
  recipients: Recipient[];
  isAdmin: boolean;
  action: (fd: FormData) => Promise<void>;
  hidden: Record<string, string | string[]>;
  count: number;
  desc?: string;
  disabledReason?: string | null;
}) {
  const active = recipients.filter((r) => r.active);
  return (
    <form action={action} className="panel stack">
      <div>
        <h2 style={{ marginBottom: 4 }}>메일로 보내기</h2>
        <p className="muted small" style={{ margin: 0 }}>
          {desc ?? `지금 보이는 ${count}건을 선택한 사람에게 보냅니다.`} 수신자끼리는 서로 주소가 보이지 않습니다.
        </p>
      </div>
      {Object.entries(hidden).flatMap(([k, v]) =>
        (Array.isArray(v) ? v : [v]).map((val, i) => <input key={`${k}-${i}`} type="hidden" name={k} value={val} />),
      )}
      {active.length ? (
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="small muted" style={{ marginBottom: 6 }}>등록된 수신자</legend>
          <div className="checks">
            {active.map((r) => (
              <label key={r.id}>
                <input type="checkbox" name="rcpt" value={r.email} />
                {r.name ? `${r.name} (${r.email})` : r.email}
              </label>
            ))}
          </div>
        </fieldset>
      ) : (
        <p className="small muted" style={{ margin: 0 }}>
          등록된 수신자가 없습니다. {isAdmin ? "관리 > 수신자에서 추가하세요." : "관리자에게 수신자 등록을 요청하세요."}
        </p>
      )}
      {isAdmin && (
        <div className="field">
          <label htmlFor="extra">직접 입력 (쉼표로 여러 명)</label>
          <input id="extra" name="extra" type="text" placeholder="name@hyundaicard.com" />
        </div>
      )}
      <div className="field">
        <label htmlFor="note">메모 (메일 상단에 표시, 선택)</label>
        <input id="note" name="note" type="text" maxLength={300} />
      </div>
      <div className="row end">
        {disabledReason && <span className="small muted">{disabledReason}</span>}
        <button className="btn-primary" type="submit" disabled={Boolean(disabledReason)}>
          메일 보내기
        </button>
      </div>
    </form>
  );
}
