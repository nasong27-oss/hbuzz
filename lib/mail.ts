import "server-only";
import nodemailer from "nodemailer";
import type { Item } from "./db";

export const SENDER = process.env.GMAIL_USER || "nasong27@gmail.com";

export function mailConfigured() {
  return Boolean(process.env.GMAIL_APP_PASSWORD);
}

export async function sendMail(opts: { to: string[]; subject: string; html: string; text: string }) {
  if (!mailConfigured()) throw new Error("GMAIL_APP_PASSWORD 환경변수가 없어 발송할 수 없습니다.");
  const transport = nodemailer.createTransport({
    host: "smtp.gmail.com",
    port: 465,
    secure: true,
    auth: { user: SENDER, pass: process.env.GMAIL_APP_PASSWORD },
  });
  // 수신자끼리 서로의 주소가 보이지 않게 BCC 로 보낸다
  await transport.sendMail({
    from: `현대카드 버즈 <${SENDER}>`,
    to: SENDER,
    bcc: opts.to,
    subject: opts.subject,
    text: opts.text,
    html: opts.html,
  });
}

const esc = (s: string) =>
  (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const SENT_COLOR: Record<string, string> = { 부정: "#B3261E", 긍정: "#1E6B4F", 중립: "#5F6B76" };

/**
 * 회사 보안메일 필터에서 깨지지 않는 형태(인라인 스타일·CSS 없이 table/font 만)의 결과 메일.
 * 기존 데일리 리포트(report.py)와 같은 원칙을 따른다.
 */
export function buildItemsEmail(opts: { heading: string; meta: string; items: Item[]; note?: string }) {
  const { heading, meta, items, note } = opts;
  const counts = { 부정: 0, 중립: 0, 긍정: 0 } as Record<string, number>;
  items.forEach((i) => (counts[i.sentiment] = (counts[i.sentiment] ?? 0) + 1));

  const rows = items
    .map((it) => {
      const c = SENT_COLOR[it.sentiment] ?? "#5F6B76";
      const date = it.post_date ?? "날짜미상";
      return `<tr>
<td width="56" valign="top"><font color="${c}"><b>${esc(it.sentiment)}</b></font></td>
<td valign="top"><b>${esc(it.title)}</b><br>
<font color="#5F6B76" size="2">${esc(it.source)} | ${esc(date)} | ${esc(it.category)}${it.app_related ? " | 앱" : ""}</font><br>
${it.reason ? `<font size="2">${esc(it.reason)}</font><br>` : ""}
<font size="2" color="#2747D6">${esc(it.link)}</font></td></tr>`;
    })
    .join("\n");

  const html = `<html><body>
<table width="100%" cellpadding="6" cellspacing="0" border="0">
<tr><td><font size="4"><b>${esc(heading)}</b></font><br>
<font color="#5F6B76" size="2">${esc(meta)}</font></td></tr>
<tr><td>부정 <b>${counts["부정"]}</b> / 중립 <b>${counts["중립"]}</b> / 긍정 <b>${counts["긍정"]}</b> (총 ${items.length}건)</td></tr>
${note ? `<tr><td><font size="2">${esc(note)}</font></td></tr>` : ""}
<tr><td><table width="100%" border="1" cellpadding="6" cellspacing="0">${rows || "<tr><td>조건에 맞는 게시글이 없습니다.</td></tr>"}</table></td></tr>
</table></body></html>`;

  const text = [
    heading,
    meta,
    `부정 ${counts["부정"]} / 중립 ${counts["중립"]} / 긍정 ${counts["긍정"]} (총 ${items.length}건)`,
    note ?? "",
    "",
    ...items.map(
      (it) =>
        `[${it.sentiment}] ${it.title}\n  ${it.source} | ${it.post_date ?? "날짜미상"} | ${it.category}\n  ${it.reason}\n  ${it.link}`,
    ),
  ].join("\n");

  return { html, text };
}

const EMAIL_RE = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/;

export function parseEmails(raw: string): { valid: string[]; invalid: string[] } {
  const parts = raw
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const valid: string[] = [];
  const invalid: string[] = [];
  for (const p of parts) (EMAIL_RE.test(p) ? valid : invalid).push(p.toLowerCase());
  return { valid: [...new Set(valid)], invalid };
}
