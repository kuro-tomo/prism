"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { logout } from "@/lib/api/client"
import { createClient } from "@/lib/supabase/client"
import { useEffect, useState } from "react"

export function Header() {
  const router = useRouter()
  const [email, setEmail] = useState<string | null>(null)

  useEffect(() => {
    const supabase = createClient()
    supabase.auth.getUser().then(({ data }) => {
      setEmail(data.user?.email ?? null)
    })
  }, [])

  const handleLogout = async () => {
    // FastAPI セッション破棄
    await logout().catch(() => {})
    // Supabase ローカルセッション破棄
    const supabase = createClient()
    await supabase.auth.signOut()
    router.push("/login")
  }

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur-sm">
      <div className="max-w-6xl mx-auto px-4 h-14 flex items-center justify-between">
        {/* ── ブランド ── */}
        <div className="flex items-center gap-2">
          <Link href="/dashboard" className="text-lg font-bold tracking-widest text-foreground hover:text-primary transition-colors">
            PRISM
          </Link>
          <Badge variant="beta" className="text-[10px]">beta</Badge>
          <span className="text-xs text-muted-foreground hidden sm:inline">経営参謀AI</span>
        </div>

        {/* ── ナビゲーション ── */}
        {email && (
          <nav className="flex items-center gap-2">
            <Link href="/profile">
              <Button variant="ghost" size="sm" className="text-xs">
                🏢 会社プロフィール
              </Button>
            </Link>
            <span className="text-xs text-muted-foreground hidden sm:inline">{email}</span>
            <Button variant="ghost" size="sm" onClick={handleLogout} className="text-xs">
              ログアウト
            </Button>
          </nav>
        )}
      </div>
    </header>
  )
}
