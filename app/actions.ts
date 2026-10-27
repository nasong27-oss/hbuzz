"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { timingSafeEqual } from "node:crypto";
import { requireRole } from "@/lib/auth";
import { q, q1, type Recipient } from "@/lib/db";
import { buildItemsEmail, parseEmails, sendMail } from "@/lib/mail";
import { parseSearch, searchItems, searchToQuery } from "@/lib/search";
import { SESSION_COOKIE, SESSION_DAYS, signSession } from "@/lib/session";
import { isYmd, kstAddDays } from "@/lib/time";

const same = (a: string, b: string) => {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
};

const safeNext = (n: unknown) =>
  typeof n === "string" && n.startsWith("/") && !n.startsWith("//") && !n.startsWith("/login") ? n : "/";

function withMsg(path: string, key: "ok" | "err", msg: string) {
  const [base, query = ""] = path.split("?");
  const u = new URLSearchParams(query);
  u.delete("ok");
  u.delete("err");
  u.set(key, msg);
  return `${base}?${u.toString()}`;
}

// ---------------------------------------------------------------------------
// 로그인
// ---------------------------------------------------------------------------

export async function login(fd: FormData) {
  const pw = String(fd.get("password") ?? "");
  const next = safeNext(fd.get("next"));
  const admin = process.env.ADMIN_PASSWORD ?? "";
  const site = process.env.SITE_PASSWORD ?? "";
  let role: "admin" | "user" | null = null;
  if (admin && same(pw, admin)) role = "admin";
  else if (site && same(pw, site)) role = "user";

  if (!role) {
    await new Promise((r) => setTimeout(r, 700)); // 무차별 대입 완화
    redirect(`/login?err=1&next=${encodeURIComponent(next)}`);
  }
  const jar = await cookies();
  jar.set(SESSION_COOKIE, await signSession(role), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_DAYS * 86400,
  });
  redirect(next);
}

export async function logout() {
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
  redirect("/login");
}

// ---------------------------------------------------------------------------
// 수신자 해석: 일반 사용자는 등록·활성 수신자만, 관리자는 직접 입력도 허용
// ---------------------------------------------------------------------------

async function resolveRecipients(fd: FormData, isAdmin: boolean) {
  const active = await q<Recipient>(`SELECT * FROM recipients WHERE active`);
  const allowed = new Set(active.map((r) => r.email.toLowerCase()));
  const picked = fd
    .getAll("rcpt")
    .map((v) => String(v).toLowerCase())
    .filter((e) => allowed.has(e));
  let extra: string[] = [];
  if (isAdmin) {
    const { valid, invalid } = parseEmails(String(fd.get("extra") ?? ""));
    if (invalid.length) return { error: `주소 형식을 확인하세요: ${invalid.join(", ")}` };
    extra = valid;
  }
  const to = [...new Set([...picked, ...extra])];
  if (!to.length) return { error: "받는 사람을 한 명 이상 선택하세요." };
  if (to.length > 50) return { error: "한 번에 50명까지 보낼 수 있습니다." };
  return { to };
}

// ---------------------------------------------------------------------------
// 조회 결과 발송
// ---------------------------------------------------------------------------

export async function sendSearch(fd: FormData) {
  const role = await requireRole("user");
  const raw: Record<string, string | string[]> = {};
  for (const k of ["kw", "from", "to", "app", "qid"]) {
    const v = fd.get(k);
    if (typeof v === "string") raw[k] = v;
  }
  raw.s = fd.getAll("s").map(String);
  raw.c = fd.getAll("c").map(String);
  const p = parseSearch(raw);
  const back = `/search?${searchToQuery(p)}`;

  const r = await resolveRecipients(fd, role === "admin");
  if ("error" in r) redirect(withMsg(back, "err", r.error!));

  const items = await searchItems(p);
  if (!items.length) redirect(withMsg(back, "err", "보낼 게시글이 없습니다. 조건을 바꿔 보세요."));

  const kwLabel = p.keywords.length ? p.keywords.join(", ") : "전체";
  const subject = `[버즈 조회] ${kwLabel} ${p.from}~${p.to} ${items.length}건`;
  const filters = [
    p.sentiment.length ? `감성 ${p.sentiment.join("/")}` : "",
    p.category.length ? `카테고리 ${p.category.join("/")}` : "",
    p.appOnly ? "앱 관련만" : "",
  ]
    .filter(Boolean)
    .join(", ");
  const { html, text } = buildItemsEmail({
    heading: `현대카드 버즈 조회: ${kwLabel}`,
    meta: `기간 ${p.from} ~ ${p.to}${filters ? ` | ${filters}` : ""}`,
    items,
    note: String(fd.get("note") ?? "").slice(0, 300) || undefined,
  });

  let error: string | null = null;
  try {
    await sendMail({ to: r.to!, subject, html, text });
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }
  await q(
    `INSERT INTO send_logs (kind, subject, recipients, status, error, query, item_count)
     VALUES ('manual', $1, $2, $3, $4, $5, $6)`,
    [subject, r.to, error ? "failed" : "success", error, JSON.stringify(p), items.length],
  );
  if (error) redirect(withMsg(back, "err", `발송 실패: ${error}`));
  redirect(withMsg(back, "ok", `${r.to!.length}명에게 ${items.length}건을 보냈습니다.`));
}

// ---------------------------------------------------------------------------
// 데일리 리포트 재발송
// ---------------------------------------------------------------------------

export async function resendReport(fd: FormData) {
  const role = await requireRole("user");
  const date = String(fd.get("date") ?? "");
  if (!isYmd(date)) redirect("/");
  const back = `/reports/${date}`;
  const r = await resolveRecipients(fd, role === "admin");
  if ("error" in r) redirect(withMsg(back, "err", r.error!));

  const rep = await q1<{ subject: string; html: string; text_body: string }>(
    `SELECT subject, html, text_body FROM reports WHERE report_date = $1`,
    [date],
  );
  if (!rep) redirect(withMsg(back, "err", "리포트를 찾을 수 없습니다."));

  const note = String(fd.get("note") ?? "").slice(0, 300);
  const html = note ? rep!.html.replace(/<body([^>]*)>/i, `<body$1><p>${escapeHtml(note)}</p>`) : rep!.html;
  const text = note ? `${note}\n\n${rep!.text_body}` : rep!.text_body;

  let error: string | null = null;
  try {
    await sendMail({ to: r.to!, subject: rep!.subject, html, text });
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }
  await q(
    `INSERT INTO send_logs (kind, subject, recipients, status, error, report_date)
     VALUES ('resend', $1, $2, $3, $4, $5)`,
    [rep!.subject, r.to, error ? "failed" : "success", error, date],
  );
  if (error) redirect(withMsg(back, "err", `발송 실패: ${error}`));
  redirect(withMsg(back, "ok", `${r.to!.length}명에게 리포트를 보냈습니다.`));
}

function escapeHtml(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ---------------------------------------------------------------------------
// 실시간 수집 요청 (Mac 수집기가 5분 주기로 처리)
// ---------------------------------------------------------------------------

export async function requestCollect(fd: FormData) {
  await requireRole("user");
  const p = parseSearch({
    kw: String(fd.get("kw") ?? ""),
    from: String(fd.get("from") ?? ""),
    to: String(fd.get("to") ?? ""),
  });
  const back = `/search?${searchToQuery(p)}`;
  if (!p.keywords.length) redirect(withMsg(back, "err", "새로 수집하려면 키워드를 입력하세요."));
  if (p.from < kstAddDays(p.to, -30)) redirect(withMsg(back, "err", "새로 수집은 최대 31일 기간까지 가능합니다."));

  const pending = await q1<{ n: number }>(
    `SELECT count(*)::int AS n FROM query_requests WHERE status IN ('pending','running')`,
  );
  if ((pending?.n ?? 0) >= 10) redirect(withMsg(back, "err", "대기 중인 요청이 많습니다. 잠시 후 다시 시도하세요."));

  const dup = await q1<{ id: number }>(
    `SELECT id FROM query_requests WHERE status IN ('pending','running')
       AND keywords = $1 AND date_from = $2 AND date_to = $3 LIMIT 1`,
    [p.keywords, p.from, p.to],
  );
  const id =
    dup?.id ??
    (
      await q1<{ id: number }>(
        `INSERT INTO query_requests (keywords, date_from, date_to) VALUES ($1, $2, $3) RETURNING id`,
        [p.keywords, p.from, p.to],
      )
    )!.id;
  redirect(
    withMsg(
      `/search?${searchToQuery({ ...p, queryId: null })}`,
      "ok",
      `수집 요청 #${id}을 등록했습니다. 집 Mac이 켜져 있으면 5분 안에 처리되고, 아래 요청 목록에서 결과를 볼 수 있습니다.`,
    ),
  );
}
