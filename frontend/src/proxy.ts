/**
 * Next.js Proxy（旧: middleware）— 認証セッション自動更新
 * Next.js 16 で middleware.ts → proxy.ts に改名。
 * @supabase/ssr が期限切れトークンをリフレッシュし Cookie を更新する。
 * 仕様書: 認証設計 §4
 */
import { createServerClient } from "@supabase/ssr"
import { NextResponse, type NextRequest } from "next/server"

// 認証不要の公開パス
const PUBLIC_PATHS = ["/login", "/auth/callback"]

export async function proxy(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request })

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll()
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          )
          supabaseResponse = NextResponse.next({ request })
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options),
          )
        },
      },
    },
  )

  // セッション取得（Cookie更新のため必須）
  const {
    data: { user },
  } = await supabase.auth.getUser()

  // ── Critical-1 修正: FastAPI deps.py は sb-access-token を読む ──────
  // @supabase/ssr は独自のチャンク形式 Cookie を管理するが、
  // FastAPI の get_current_user() は `sb-access-token` を期待する（deps.py L98）。
  // セッションから access_token を取り出して手動で Cookie を同期する。
  if (user) {
    const { data: { session } } = await supabase.auth.getSession()
    if (session?.access_token) {
      supabaseResponse.cookies.set("sb-access-token", session.access_token, {
        path: "/",
        sameSite: "lax",
        httpOnly: true, // HttpOnly必須（XSSによるトークン盗取防止）
        // FastAPIはサーバー間通信でCookieを自動受信するためhttpOnly: trueで問題なし
        secure: process.env.NODE_ENV === "production",
        maxAge: session.expires_in ?? 3600,
      })
    }
  }

  const { pathname } = request.nextUrl

  // 未認証 → /login へリダイレクト（公開パスは除外）
  if (!user && !PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    const url = request.nextUrl.clone()
    url.pathname = "/login"
    return NextResponse.redirect(url)
  }

  // 認証済みで /login アクセス → /dashboard へリダイレクト
  if (user && pathname === "/login") {
    const url = request.nextUrl.clone()
    url.pathname = "/dashboard"
    return NextResponse.redirect(url)
  }

  return supabaseResponse
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
}
