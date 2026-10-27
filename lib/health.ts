import "server-only";
import { q, q1 } from "./db";
import { kstNowMinutes, kstToday } from "./time";

export type Warning = { level: "warn" | "err"; text: string };

/** 운영 경고: 대시보드 상단과 관리 > 운영 탭에 공통으로 표시 */
export async function healthWarnings(): Promise<Warning[]> {
  const out: Warning[] = [];
  const today = kstToday();

  const sent = await q1<{ n: number }>(
    `SELECT count(*)::int AS n FROM send_logs WHERE kind='daily' AND status='success' AND report_date=$1`,
    [today],
  );
  if (kstNowMinutes() >= 8 * 60 + 5 && !sent?.n) {
    const rep = await q1<{ n: number }>(`SELECT count(*)::int AS n FROM reports WHERE report_date=$1`, [today]);
    out.push({
      level: "err",
      text: rep?.n
        ? "오늘 리포트는 만들어졌지만 아침 메일 발송 기록이 없습니다. 리포트 화면에서 다시 보낼 수 있습니다."
        : "오늘 리포트가 아직 없습니다. 집 Mac이 켜져 있고 인터넷에 연결돼 있는지 확인하세요.",
    });
  }

  const lastGen = await q1<{ status: string; error: string | null }>(
    `SELECT status, error FROM runs WHERE kind='generate' ORDER BY started_at DESC LIMIT 1`,
  );
  if (lastGen?.status === "failed") {
    out.push({ level: "err", text: `마지막 리포트 생성이 실패했습니다: ${lastGen.error ?? "원인 미상"}` });
  }

  const fb = await q1<{ fallback: number }>(
    `SELECT COALESCE((stats->>'fallback')::int, 0) AS fallback FROM reports WHERE report_date=$1`,
    [today],
  );
  if (fb && fb.fallback > 0) {
    out.push({
      level: "warn",
      text: `오늘 리포트에서 ${fb.fallback}건이 AI 대신 키워드 규칙으로 분류됐습니다. Claude API 키나 사용량 한도를 확인하세요.`,
    });
  }

  const stale = await q<{ id: number }>(
    `SELECT id FROM query_requests WHERE status='pending' AND created_at < now() - interval '15 minutes'`,
  );
  if (stale.length) {
    out.push({
      level: "warn",
      text: `수집 요청 ${stale.length}건이 15분 넘게 대기 중입니다. 집 Mac이 꺼져 있거나 잠자기 상태일 수 있습니다.`,
    });
  }
  return out;
}
