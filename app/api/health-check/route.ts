import { timingSafeEqual } from "node:crypto";
import { runHealthCheck } from "@/lib/alert";

export const dynamic = "force-dynamic";

// Vercel 크론은 CRON_SECRET 이 설정돼 있으면 "Authorization: Bearer <CRON_SECRET>" 을 자동으로 붙인다.
// GitHub Actions 도 같은 헤더로 호출한다.
function authorized(req: Request) {
  const secret = process.env.CRON_SECRET;
  if (!secret) return false;
  const got = Buffer.from(req.headers.get("authorization") ?? "");
  const want = Buffer.from(`Bearer ${secret}`);
  return got.length === want.length && timingSafeEqual(got, want);
}

function siteUrl(req: Request) {
  const prod = process.env.VERCEL_PROJECT_PRODUCTION_URL;
  return prod ? `https://${prod}` : new URL(req.url).origin;
}

export async function GET(req: Request) {
  if (!authorized(req)) return Response.json({ ok: false, reason: "unauthorized" }, { status: 401 });
  try {
    const res = await runHealthCheck(siteUrl(req));
    return Response.json({ ok: true, ...res });
  } catch (e) {
    return Response.json({ ok: false, reason: e instanceof Error ? e.message : String(e) }, { status: 500 });
  }
}
