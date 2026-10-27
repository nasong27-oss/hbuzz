import "server-only";
import { Pool, types } from "pg";

// DATE 는 문자열(YYYY-MM-DD)로 그대로 받는다 — 타임존 변환으로 하루 밀리는 문제 방지
types.setTypeParser(1082, (v: string) => v);

declare global {
  // eslint-disable-next-line no-var
  var __hbuzzPool: Pool | undefined;
}

function pool(): Pool {
  if (!process.env.DATABASE_URL) {
    throw new Error("DATABASE_URL 환경변수가 없습니다. Vercel 프로젝트 설정에서 Neon DB를 연결하세요.");
  }
  if (!global.__hbuzzPool) {
    global.__hbuzzPool = new Pool({
      connectionString: process.env.DATABASE_URL,
      max: 3,
      idleTimeoutMillis: 10_000,
      connectionTimeoutMillis: 10_000,
    });
  }
  return global.__hbuzzPool;
}

export function dbConfigured() {
  return Boolean(process.env.DATABASE_URL);
}

export async function q<T = Record<string, unknown>>(text: string, params: unknown[] = []): Promise<T[]> {
  const res = await pool().query(text, params);
  return res.rows as T[];
}

export async function q1<T = Record<string, unknown>>(text: string, params: unknown[] = []): Promise<T | null> {
  const rows = await q<T>(text, params);
  return rows[0] ?? null;
}

// ---------------------------------------------------------------------------
// 공용 타입
// ---------------------------------------------------------------------------

export type Sentiment = "부정" | "중립" | "긍정";

export type Item = {
  id: number;
  link: string;
  source: string;
  title: string;
  snippet: string;
  post_date: string | null;
  date_unknown: boolean;
  sentiment: Sentiment;
  category: string;
  type: string;
  app_related: boolean;
  reason: string;
  fallback: boolean;
  origin: string;
  report_date: string | null;
};

export type ReportRow = {
  id: number;
  report_date: string;
  subject: string;
  issue_summary: Partial<Record<Sentiment, string[]>>;
  stats: Record<string, unknown>;
  errors: Record<string, string>;
  keywords: string[];
  created_at: string;
};

export type Recipient = {
  id: number;
  email: string;
  name: string;
  active: boolean;
  receive_daily: boolean;
  note: string;
};

export const ITEM_COLS = `id, link, source, title, snippet, post_date, date_unknown, sentiment,
  category, type, app_related, reason, fallback, origin, report_date`;
