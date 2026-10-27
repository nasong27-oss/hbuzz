import "server-only";
import { ITEM_COLS, q, type Item } from "./db";
import { isYmd, kstAddDays, kstToday } from "./time";

export const SENTIMENTS = ["부정", "중립", "긍정"] as const;
export const CATEGORIES = ["결제", "챗봇", "기타"] as const;

export type SearchParams = {
  keywords: string[];
  from: string;
  to: string;
  sentiment: string[];
  category: string[];
  appOnly: boolean;
  queryId: number | null;
};

type Raw = Record<string, string | string[] | undefined>;

const many = (v: string | string[] | undefined) => (Array.isArray(v) ? v : v ? [v] : []);

export function parseKeywords(raw: string): string[] {
  return [
    ...new Set(
      raw
        .split(/[,\n]/)
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  ].slice(0, 8);
}

export function parseSearch(sp: Raw): SearchParams {
  const today = kstToday();
  const kw = typeof sp.kw === "string" ? sp.kw : "";
  let from = isYmd(sp.from) ? sp.from : kstAddDays(today, -6);
  let to = isYmd(sp.to) ? sp.to : today;
  if (from > to) [from, to] = [to, from];
  const qid = typeof sp.qid === "string" && /^\d+$/.test(sp.qid) ? Number(sp.qid) : null;
  return {
    keywords: parseKeywords(kw),
    from,
    to,
    sentiment: many(sp.s).filter((s) => (SENTIMENTS as readonly string[]).includes(s)),
    category: many(sp.c).filter((c) => (CATEGORIES as readonly string[]).includes(c)),
    appOnly: sp.app === "1",
    queryId: qid,
  };
}

export function searchToQuery(p: SearchParams): string {
  const u = new URLSearchParams();
  if (p.keywords.length) u.set("kw", p.keywords.join(", "));
  u.set("from", p.from);
  u.set("to", p.to);
  p.sentiment.forEach((s) => u.append("s", s));
  p.category.forEach((c) => u.append("c", c));
  if (p.appOnly) u.set("app", "1");
  if (p.queryId) u.set("qid", String(p.queryId));
  return u.toString();
}

/**
 * 아카이브 검색. 기간은 게시일 기준, 게시일을 모르면 수집일 기준으로 판단한다.
 * queryId 가 있으면 그 실시간 조회 요청으로 수집된 글만.
 */
export async function searchItems(p: SearchParams, limit = 300): Promise<Item[]> {
  const where: string[] = [];
  const params: unknown[] = [];
  const add = (v: unknown) => {
    params.push(v);
    return `$${params.length}`;
  };

  where.push(
    `COALESCE(post_date, (collected_at AT TIME ZONE 'Asia/Seoul')::date) BETWEEN ${add(p.from)}::date AND ${add(p.to)}::date`,
  );
  if (p.keywords.length) {
    const pats = p.keywords.map((k) => `%${k.replace(/[\\%_]/g, (m) => "\\" + m)}%`);
    const ph = add(pats);
    where.push(`(title ILIKE ANY(${ph}) OR snippet ILIKE ANY(${ph}) OR reason ILIKE ANY(${ph}))`);
  }
  if (p.sentiment.length) where.push(`sentiment = ANY(${add(p.sentiment)})`);
  if (p.category.length) where.push(`category = ANY(${add(p.category)})`);
  if (p.appOnly) where.push(`app_related`);
  if (p.queryId) where.push(`id IN (SELECT item_id FROM query_request_items WHERE query_id = ${add(p.queryId)})`);

  return q<Item>(
    `SELECT ${ITEM_COLS} FROM items WHERE ${where.join(" AND ")}
     ORDER BY CASE sentiment WHEN '부정' THEN 0 WHEN '중립' THEN 1 ELSE 2 END,
              post_date DESC NULLS LAST, collected_at DESC
     LIMIT ${Number(limit)}`,
    params,
  );
}
