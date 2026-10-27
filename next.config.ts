import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 어드민의 "DB 초기화"가 런타임에 읽는 스키마 파일을 서버리스 번들에 포함
  outputFileTracingIncludes: { "/admin": ["./db/schema.sql"] },
  serverExternalPackages: ["nodemailer"],
};

export default nextConfig;
