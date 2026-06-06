import type { NextConfig } from "next"

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

const nextConfig: NextConfig = {
  // FastAPI バックエンドへのプロキシ（Cookie認証を同一ドメインで透過させる）
  // 仕様書: Next.js移行設計 §3 — 認証境界はCookieベースを維持
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_URL}/:path*`,
      },
    ]
  },
}

export default nextConfig
