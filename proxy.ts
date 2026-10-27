import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, verifySession } from "@/lib/session";

// 사이트 전체 비밀번호 보호. /admin 은 어드민 역할만.
export async function proxy(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  // 로그인 화면, 크론용 점검 엔드포인트(자체 비밀키 인증)는 통과
  if (pathname === "/login" || pathname === "/api/health-check") return NextResponse.next();

  const role = await verifySession(req.cookies.get(SESSION_COOKIE)?.value);
  if (!role || (pathname.startsWith("/admin") && role !== "admin")) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    url.searchParams.set("next", pathname + search);
    if (role && pathname.startsWith("/admin")) url.searchParams.set("need", "admin");
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg|robots.txt).*)"],
};
