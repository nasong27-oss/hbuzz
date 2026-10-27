import "server-only";
import { q } from "./db";

// collector/db.py 의 CLASSIFIER_MODES / CLAUDE_MODELS 와 같은 목록을 유지할 것
export const CLASSIFIER_MODES = [
  { value: "auto", label: "구독 우선, 실패 시 API 키", desc: "집 Mac의 Claude Code(구독)로 분류하고, 실패하면 API 키로 넘어갑니다." },
  { value: "claude-code", label: "구독만", desc: "API 키를 쓰지 않습니다. 구독 경로가 실패하면 그날은 키워드 규칙으로 분류합니다." },
  { value: "api", label: "API 키만", desc: "구독을 쓰지 않고 API 키로만 분류합니다(종량 과금)." },
] as const;

export const CLAUDE_MODELS = [
  { value: "claude-opus-5-5", label: "Opus 5.5", desc: "가장 정확, 가장 비쌈" },
  { value: "claude-sonnet-5-5", label: "Sonnet 5.5", desc: "현재 기본값" },
  { value: "claude-haiku-5-5", label: "Haiku 5.5", desc: "가장 저렴, 단순 분류용" },
] as const;

export type ClassifierMode = (typeof CLASSIFIER_MODES)[number]["value"];
export type ClaudeModel = (typeof CLAUDE_MODELS)[number]["value"];

export const isMode = (v: unknown): v is ClassifierMode => CLASSIFIER_MODES.some((m) => m.value === v);
export const isModel = (v: unknown): v is ClaudeModel => CLAUDE_MODELS.some((m) => m.value === v);

export async function getSettings() {
  const rows = await q<{ key: string; value: string; updated_at: string }>(
    `SELECT key, value, updated_at FROM settings WHERE key IN ('classifier_mode','claude_model')`,
  );
  const get = (k: string) => rows.find((r) => r.key === k);
  return { mode: get("classifier_mode"), model: get("claude_model") };
}

export async function saveSetting(key: "classifier_mode" | "claude_model", value: string) {
  await q(
    `INSERT INTO settings (key, value) VALUES ($1, $2)
     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()`,
    [key, value],
  );
}
