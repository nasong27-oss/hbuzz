import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { SESSION_COOKIE, verifySession, type Role } from "./session";

export async function currentRole(): Promise<Role | null> {
  const jar = await cookies();
  return verifySession(jar.get(SESSION_COOKIE)?.value);
}

/** 서버 액션 안에서 다시 확인(프록시를 우회한 직접 호출 방어). */
export async function requireRole(need: Role = "user"): Promise<Role> {
  const role = await currentRole();
  if (!role) redirect("/login");
  if (need === "admin" && role !== "admin") redirect("/login?need=admin");
  return role;
}
