/**
 * Supabase Auth コールバックルート（Magic Link OTP 確認）
 * Supabase が emailRedirectTo で戻ってくる先。
 * code を exchangeCodeForSession でセッションに変換し /dashboard へリダイレクト。
 */
import { NextResponse } from "next/server"
import { createClient } from "@/lib/supabase/server"

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get("code")
  const next = searchParams.get("next") ?? "/dashboard"

  if (code) {
    const supabase = await createClient()
    const { error } = await supabase.auth.exchangeCodeForSession(code)
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`)
    }
  }

  // エラー時は /login にリダイレクト
  return NextResponse.redirect(`${origin}/login?error=callback_failed`)
}
