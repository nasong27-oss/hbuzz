// 세션 토큰: "<role>.<만료epoch초>.<HMAC-SHA256>" — proxy(엣지/노드)와 서버 양쪽에서 쓰므로 Web Crypto만 사용.
export type Role = "user" | "admin";
export const SESSION_COOKIE = "hb_session";
export const SESSION_DAYS = 30;

function secret() {
  const s = process.env.AUTH_SECRET;
  if (!s || s.length < 16) throw new Error("AUTH_SECRET(16자 이상)이 설정되지 않았습니다.");
  return s;
}

async function hmac(data: string) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
  return Buffer.from(new Uint8Array(sig)).toString("base64url");
}

export async function signSession(role: Role) {
  const exp = Math.floor(Date.now() / 1000) + SESSION_DAYS * 86400;
  const body = `${role}.${exp}`;
  return `${body}.${await hmac(body)}`;
}

export async function verifySession(token: string | undefined | null): Promise<Role | null> {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [role, exp, sig] = parts;
  if (role !== "user" && role !== "admin") return null;
  if (!/^\d+$/.test(exp) || Number(exp) < Date.now() / 1000) return null;
  let expected: string;
  try {
    expected = await hmac(`${role}.${exp}`);
  } catch {
    return null;
  }
  if (expected.length !== sig.length) return null;
  let diff = 0;
  for (let i = 0; i < sig.length; i++) diff |= expected.charCodeAt(i) ^ sig.charCodeAt(i);
  return diff === 0 ? (role as Role) : null;
}
