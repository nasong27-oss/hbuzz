"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

type Phase = "idle" | "running" | "done" | "error";

function useTimer(active: boolean) {
  const [sec, setSec] = useState(0);
  const t0 = useRef(0);
  useEffect(() => {
    if (!active) return;
    t0.current = Date.now();
    setSec(0);
    const id = setInterval(() => setSec(Math.round((Date.now() - t0.current) / 1000)), 1000);
    return () => clearInterval(id);
  }, [active]);
  return sec;
}

async function callCloud(method: "GET" | "POST", action: string, body?: unknown) {
  const res = await fetch(`/api/cloud?action=${action}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  let data: Record<string, unknown> = {};
  try {
    data = await res.json();
  } catch {
    // Vercel 시간 초과(504) 등은 JSON 이 아니다
  }
  if (!res.ok || data.ok === false) {
    const reason =
      (data.reason as string) ??
      (res.status === 504 ? "5분 실행 제한에 걸렸습니다. 운영 탭의 최근 실행에서 결과를 확인하세요." : `HTTP ${res.status}`);
    throw new Error(reason);
  }
  return data;
}

export function CloudPanel({ hasPrimaryToday, hasDiag }: { hasPrimaryToday: boolean; hasDiag: boolean }) {
  const router = useRouter();
  const [diag, setDiag] = useState<{ phase: Phase; msg: string }>({ phase: "idle", msg: "" });
  const [run, setRun] = useState<{ phase: Phase; msg: string; link?: string }>({ phase: "idle", msg: "" });
  const [send, setSend] = useState(false);
  const diagSec = useTimer(diag.phase === "running");
  const runSec = useTimer(run.phase === "running");
  const busy = diag.phase === "running" || run.phase === "running";

  async function doDiag() {
    setDiag({ phase: "running", msg: "" });
    try {
      const d = await callCloud("GET", "diag");
      const results = (d.report as { results: Record<string, { status: string }> }).results;
      const ok = Object.values(results).filter((r) => r.status === "ok").length;
      setDiag({ phase: "done", msg: `${Object.keys(results).length}개 사이트 중 ${ok}곳에서 수집됐습니다.` });
      router.refresh();
    } catch (e) {
      setDiag({ phase: "error", msg: e instanceof Error ? e.message : String(e) });
    }
  }

  async function doRun() {
    const what = send ? "백업 리포트를 만들고 아침 수신자 전원에게 보냅니다." : "백업 리포트를 만듭니다(발송 안 함).";
    if (!window.confirm(`${what}\n최대 5분 걸립니다. 진행할까요?`)) return;
    setRun({ phase: "running", msg: "" });
    try {
      const d = (await callCloud("POST", "run", { send })) as {
        report_date: string;
        ok_sources: string[];
        failed_sources: string[];
        stats: { consumer?: number; negative?: number; sent_to?: number; send_error?: string };
      };
      const s = d.stats;
      const sentMsg = send ? (s.send_error ? ` 발송 실패: ${s.send_error}` : ` ${s.sent_to ?? 0}명에게 보냈습니다.`) : "";
      setRun({
        phase: s.send_error ? "error" : "done",
        msg: `소비자여론 ${s.consumer ?? 0}건(부정 ${s.negative ?? 0}). 수집된 사이트 ${d.ok_sources.length}곳, 빠진 사이트 ${d.failed_sources.length}곳.${sentMsg}`,
        link: `/reports/${d.report_date}`,
      });
      router.refresh();
    } catch (e) {
      setRun({ phase: "error", msg: e instanceof Error ? e.message : String(e) });
    }
  }

  return (
    <div className="panel stack">
      <div>
        <h2 style={{ marginBottom: 4 }}>클라우드 백업 수집</h2>
        <p className="small muted" style={{ margin: 0, maxWidth: "70ch" }}>
          집 Mac이 꺼졌거나 실패한 날, Vercel 서울 서버에서 대신 수집해 리포트를 만듭니다. 클라우드에서 막히는 사이트는
          빠지므로 리포트에 &lsquo;백업&rsquo;으로 표시됩니다. 진단을 먼저 해 두면 막힌 사이트를 건너뛰어 더 빨리 끝납니다.
        </p>
      </div>

      <div className="row">
        <button type="button" onClick={doDiag} disabled={busy}>
          {diag.phase === "running" ? `진단 중… ${diagSec}초` : hasDiag ? "진단 다시 실행" : "클라우드 진단 실행"}
        </button>
        {diag.msg && (
          <span className={`small ${diag.phase === "error" ? "sent-neg" : ""}`} role="status">
            {diag.msg}
          </span>
        )}
      </div>

      <div className="row" style={{ alignItems: "center" }}>
        <label style={{ fontWeight: 500, display: "inline-flex", gap: 6, alignItems: "center", color: "var(--ink)" }}>
          <input type="checkbox" checked={send} onChange={(e) => setSend(e.target.checked)} disabled={busy} />
          만든 뒤 아침 수신자에게 발송
        </label>
        <button type="button" className="btn-primary" onClick={doRun} disabled={busy || hasPrimaryToday}>
          {run.phase === "running" ? `수집 중… ${runSec}초` : "지금 클라우드로 수집"}
        </button>
      </div>
      {hasPrimaryToday && (
        <p className="small muted" style={{ margin: 0 }}>
          오늘은 집 Mac 리포트가 이미 있어 백업이 필요 없습니다.
        </p>
      )}
      {run.msg && (
        <div className={`flash ${run.phase === "error" ? "err" : "ok"}`} role="status">
          {run.msg}{" "}
          {run.link && (
            <a href={run.link} style={{ fontWeight: 600 }}>
              리포트 보기
            </a>
          )}
        </div>
      )}
    </div>
  );
}
