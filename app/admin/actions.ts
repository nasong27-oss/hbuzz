"use server";

import { readFile } from "node:fs/promises";
import path from "node:path";
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { requireRole } from "@/lib/auth";
import { q } from "@/lib/db";
import { parseEmails, SENDER, sendMail } from "@/lib/mail";

const back = (tab: string, key: "ok" | "err", msg: string) =>
  redirect(`/admin?tab=${tab}&${key}=${encodeURIComponent(msg)}`);

const id = (fd: FormData) => {
  const v = Number(fd.get("id"));
  if (!Number.isInteger(v) || v <= 0) throw new Error("잘못된 요청");
  return v;
};

// ── 수신자 ─────────────────────────────────────────

export async function addRecipients(fd: FormData) {
  await requireRole("admin");
  const { valid, invalid } = parseEmails(String(fd.get("emails") ?? ""));
  const name = String(fd.get("name") ?? "").trim().slice(0, 60);
  if (invalid.length) back("recipients", "err", `주소 형식을 확인하세요: ${invalid.join(", ")}`);
  if (!valid.length) back("recipients", "err", "추가할 이메일을 입력하세요.");
  const daily = fd.get("daily") === "1";
  let added = 0;
  for (const email of valid) {
    const rows = await q(
      `INSERT INTO recipients (email, name, receive_daily) VALUES ($1, $2, $3)
       ON CONFLICT (email) DO NOTHING RETURNING id`,
      [email, valid.length === 1 ? name : "", daily],
    );
    added += rows.length;
  }
  revalidatePath("/admin");
  const skipped = valid.length - added;
  back("recipients", "ok", `${added}명 추가했습니다.${skipped ? ` 이미 있는 ${skipped}명은 건너뛰었습니다.` : ""}`);
}

export async function toggleRecipient(fd: FormData) {
  await requireRole("admin");
  const field = fd.get("field") === "receive_daily" ? "receive_daily" : "active";
  await q(`UPDATE recipients SET ${field} = NOT ${field} WHERE id = $1`, [id(fd)]);
  revalidatePath("/admin");
  redirect("/admin?tab=recipients");
}

export async function renameRecipient(fd: FormData) {
  await requireRole("admin");
  await q(`UPDATE recipients SET name = $2 WHERE id = $1`, [id(fd), String(fd.get("name") ?? "").trim().slice(0, 60)]);
  revalidatePath("/admin");
  redirect("/admin?tab=recipients");
}

export async function deleteRecipient(fd: FormData) {
  await requireRole("admin");
  const rows = await q<{ email: string }>(`DELETE FROM recipients WHERE id = $1 RETURNING email`, [id(fd)]);
  revalidatePath("/admin");
  back("recipients", "ok", rows[0] ? `${rows[0].email}을 삭제했습니다.` : "이미 삭제된 수신자입니다.");
}

// ── 키워드 ─────────────────────────────────────────

export async function addKeywords(fd: FormData) {
  await requireRole("admin");
  const list = [
    ...new Set(
      String(fd.get("keywords") ?? "")
        .split(/[,\n]/)
        .map((s) => s.trim())
        .filter((s) => s && s.length <= 40),
    ),
  ];
  if (!list.length) back("keywords", "err", "추가할 키워드를 입력하세요.");
  for (const k of list) {
    await q(`INSERT INTO keywords (keyword) VALUES ($1) ON CONFLICT (keyword) DO NOTHING`, [k]);
  }
  revalidatePath("/admin");
  back("keywords", "ok", `${list.length}개 키워드를 반영했습니다. 내일 아침 수집부터 적용됩니다.`);
}

export async function toggleKeyword(fd: FormData) {
  await requireRole("admin");
  await q(`UPDATE keywords SET active = NOT active WHERE id = $1`, [id(fd)]);
  revalidatePath("/admin");
  redirect("/admin?tab=keywords");
}

export async function deleteKeyword(fd: FormData) {
  await requireRole("admin");
  await q(`DELETE FROM keywords WHERE id = $1`, [id(fd)]);
  revalidatePath("/admin");
  redirect("/admin?tab=keywords");
}

// ── 운영 ─────────────────────────────────────────

export async function initDb() {
  await requireRole("admin");
  try {
    const sql = await readFile(path.join(process.cwd(), "db", "schema.sql"), "utf8");
    const statements = sql
      .split("\n")
      .filter((l) => !l.trim().startsWith("--"))
      .join("\n")
      .split(/;\s*\n/)
      .map((s) => s.trim())
      .filter(Boolean);
    for (const st of statements) await q(st);
  } catch (e) {
    back("ops", "err", `DB 초기화 실패: ${e instanceof Error ? e.message : String(e)}`);
  }
  revalidatePath("/", "layout");
  back("ops", "ok", "DB 테이블을 만들었습니다(이미 있으면 그대로 유지).");
}

export async function sendTestMail() {
  await requireRole("admin");
  try {
    await sendMail({
      to: [SENDER],
      subject: "[버즈] 발송 테스트",
      html: "<p>웹 대시보드에서 보낸 발송 테스트 메일입니다.</p>",
      text: "웹 대시보드에서 보낸 발송 테스트 메일입니다.",
    });
  } catch (e) {
    back("ops", "err", `테스트 발송 실패: ${e instanceof Error ? e.message : String(e)}`);
  }
  back("ops", "ok", `${SENDER}로 테스트 메일을 보냈습니다.`);
}
