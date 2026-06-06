"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { createClient } from "@/lib/supabase/client"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

export default function LoginClient() {
  const router = useRouter()
  const [email, setEmail] = useState("")
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState("")

  /* ── Magic Link コールバック処理 ────────────────────────────
     Supabase が Site URL にリダイレクト後、fragment に token が残る。
     @supabase/ssr が自動的に Cookie に変換するが、
     念のため URL fragment を受け取って Supabase セッションを確立する。 */
  useEffect(() => {
    const supabase = createClient()

    // URL フラグメントの access_token を検出して Supabase セッションを設定
    supabase.auth.onAuthStateChange((event, session) => {
      if (event === "SIGNED_IN" && session) {
        router.replace("/dashboard")
      }
    })
  }, [router])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email) return
    setSending(true)
    setError("")
    try {
      const supabase = createClient()
      const { error } = await supabase.auth.signInWithOtp({
        email,
        options: {
          emailRedirectTo: `${window.location.origin}/auth/callback`,
        },
      })
      if (error) throw error
      setSent(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "メール送信に失敗しました")
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-6">
        {/* ── ブランド ── */}
        <div className="text-center space-y-3">
          <div className="text-5xl font-bold tracking-widest text-primary">PRISM</div>
          <div className="space-y-0.5 text-xs text-muted-foreground font-mono">
            <div><span className="text-primary font-semibold">P</span>arallel</div>
            <div><span className="text-primary font-semibold">R</span>easoning</div>
            <div><span className="text-primary font-semibold">I</span>ntelligence</div>
            <div><span className="text-primary font-semibold">S</span>ystem for</div>
            <div><span className="text-primary font-semibold">M</span>anagement</div>
          </div>
          <Badge variant="beta" className="mx-auto">beta</Badge>
        </div>

        {/* ── ログインカード ── */}
        <Card>
          <CardContent className="pt-5 space-y-4">
            <div>
              <h2 className="text-base font-semibold text-foreground">ログイン</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                登録メールアドレスに認証リンクを送信いたします。
              </p>
            </div>

            {sent ? (
              <div className="text-center space-y-2 py-4">
                <div className="text-2xl">📧</div>
                <p className="text-sm text-foreground">認証リンクを送信いたしました。</p>
                <p className="text-xs text-muted-foreground">
                  {email} のメールをご確認ください。
                </p>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-3">
                <div className="space-y-1.5">
                  <label htmlFor="email" className="text-xs text-muted-foreground">
                    メールアドレス
                  </label>
                  <input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="president@example.com"
                    required
                    autoComplete="email"
                    className="w-full bg-input border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
                  />
                </div>

                {error && (
                  <p className="text-xs text-destructive">{error}</p>
                )}

                <Button
                  type="submit"
                  className="w-full"
                  disabled={sending || !email}
                >
                  {sending ? "送信中…" : "認証リンクを送信"}
                </Button>
              </form>
            )}
          </CardContent>
        </Card>

        <p className="text-center text-xs text-muted-foreground">
          経営参謀AI PRISM — Shinonome Engineering LLC
        </p>
      </div>
    </div>
  )
}
