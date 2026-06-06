/**
 * Supabase Browser Client
 * @supabase/ssr を使用。Cookie を自動管理する。
 * 仕様書: 認証設計 §4 — Supabase Auth (Magic Link)
 */
import { createBrowserClient } from "@supabase/ssr"

export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  )
}
