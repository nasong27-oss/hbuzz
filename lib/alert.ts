import "server-only";
import { q, q1 } from "./db";
import { SENDER, sendMail } from "./mail";
import { fmtDateTime, kstNowMinutes, kstToday } from "./time";

export const ALERT_AFTER_MIN = 8 * 60; // 08:00 KST 이후에만 판단
export const alertRecipients = () =>
  (process.env.ALERT_TO || SENDER)
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);

export type Diagnosis = { ok: boolean; title: string; detail: string[] };

/** 오늘 아침 리포트가 정상 발송됐는지, 아니라면 어디서 멈췄는지 판단한다. */
export async function diagnoseToday(): Promise<Diagnosis> {
  const today = kstToday();
  const sent = await q1<{ n: number; rcpt: number }>(
    `SELECT count(*)::int AS n, max(cardinality(recipients))::int AS rcpt FROM send_logs
     WHERE kind IN ('daily','backup') AND status='success' AND report_date=$1`,
    [today],
  );
  if (sent?.n) return { ok: true, title: "정상 발송됨", detail: [`오늘 리포트가 ${sent.rcpt}명에게 발송됐습니다.`] };

  const [report, gen, failedSend, lastAny] = await Promise.all([
    q1<{ subject: string; created_at: string }>(`SELECT subject, created_at FROM reports WHERE report_date=$1`, [today]),
    q1<{ status: string; error: string | null; started_at: string; finished_at: string | null }>(
      `SELECT status, error, started_at, finished_at FROM runs
       WHERE kind='generate' AND (started_at AT TIME ZONE 'Asia/Seoul')::date = $1
       ORDER BY started_at DESC LIMIT 1`,
      [today],
    ),
    q1<{ error: string | null; created_at: string }>(
      `SELECT error, created_at FROM send_logs WHERE kind IN ('daily','backup') AND status='failed'
       AND report_date=$1 ORDER BY created_at DESC LIMIT 1`,
      [today],
    ),
    q1<{ kind: string; started_at: string }>(`SELECT kind, started_at FROM runs ORDER BY started_at DESC LIMIT 1`),
  ]);
  const last = lastAny ? `집 Mac의 마지막 활동: ${fmtDateTime(lastAny.started_at)} (${lastAny.kind})` : "집 Mac 활동 기록이 없습니다.";

  if (report && failedSend) {
    return {
      ok: false,
      title: "리포트는 만들어졌지만 메일 발송이 실패했습니다",
      detail: [`발송 오류: ${failedSend.error ?? "원인 미상"}`, "웹 리포트 화면에서 다시 보낼 수 있습니다. Gmail 앱 비밀번호가 바뀌었는지 확인하세요."],
    };
  }
  if (report) {
    return {
      ok: false,
      title: "리포트는 만들어졌지만 08:00 발송이 실행되지 않았습니다",
      detail: [`리포트 생성: ${fmtDateTime(report.created_at)}`, "웹 리포트 화면에서 바로 보낼 수 있습니다. 08:00 발송 잡(launchd)을 확인하세요.", last],
    };
  }
  if (gen?.status === "failed") {
    return {
      ok: false,
      title: "리포트 생성이 실패했습니다",
      detail: [`오류: ${gen.error ?? "원인 미상"}`, `시작: ${fmtDateTime(gen.started_at)}`, "관리 > 운영에서 클라우드 백업 수집으로 대신 만들 수 있습니다."],
    };
  }
  if (gen?.status === "running") {
    return {
      ok: false,
      title: "리포트 생성이 아직 끝나지 않았습니다",
      detail: [`시작: ${fmtDateTime(gen.started_at)}`, "수집이 지연되고 있습니다(네트워크 불안정 가능). 발송 잡은 최대 40분까지 기다립니다.", last],
    };
  }
  return {
    ok: false,
    title: "오늘 리포트 생성이 시작되지 않았습니다",
    detail: [
      "집 Mac이 꺼졌거나 잠자기·네트워크 끊김 상태일 가능성이 큽니다.",
      last,
      "관리 > 운영에서 클라우드 백업 수집으로 대신 만들 수 있습니다.",
    ],
  };
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export async function sendAlert(d: Diagnosis, opts: { siteUrl?: string; test?: boolean } = {}) {
  const today = kstToday();
  const to = alertRecipients();
  const subject = `${opts.test ? "[테스트] " : ""}[버즈 경고] ${today} 아침 리포트 미발송: ${d.title}`;
  const link = opts.siteUrl ? `${opts.siteUrl.replace(/\/$/, "")}/admin?tab=ops` : "";
  const html = `<html><body><table cellpadding="6"><tr><td>
<font size="4"><b>${esc(d.title)}</b></font><br>
${d.detail.map((x) => esc(x)).join("<br>\n")}
${link ? `<br><br>운영 화면: ${esc(link)}` : ""}
</td></tr></table></body></html>`;
  const text = [d.title, ...d.detail, link ? `\n운영 화면: ${link}` : ""].join("\n");
  await sendMail({ to, subject, html, text });
  await q(
    `INSERT INTO send_logs (kind, subject, recipients, status, report_date)
     VALUES ($1, $2, $3, 'success', $4)`,
    [opts.test ? "alert_test" : "alert", subject, to, today],
  );
  return { to, subject };
}

/** 크론·GitHub Actions 가 호출. 08:00 이후, 미발송이고, 오늘 알림을 아직 안 보냈을 때만 보낸다. */
export async function runHealthCheck(siteUrl?: string) {
  if (kstNowMinutes() < ALERT_AFTER_MIN) return { action: "skip", reason: "08:00 이전" };
  const d = await diagnoseToday();
  if (d.ok) return { action: "none", reason: d.title };
  const already = await q1<{ n: number }>(
    `SELECT count(*)::int AS n FROM send_logs WHERE kind='alert' AND status='success' AND report_date=$1`,
    [kstToday()],
  );
  if (already?.n) return { action: "skip", reason: "오늘 이미 알림 보냄", diagnosis: d.title };
  const r = await sendAlert(d, { siteUrl });
  return { action: "alerted", to: r.to, diagnosis: d.title };
}
