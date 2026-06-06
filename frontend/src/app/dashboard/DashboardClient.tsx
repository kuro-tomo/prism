"use client"

import { useCallback, useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Header } from "@/components/Header"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  createDeliberation,
  getProfile,
  listDeliberations,
  type SessionListItem,
} from "@/lib/api/client"
import { useSTT } from "@/hooks/useSTT"
import { Mic, MicOff, Loader2 } from "lucide-react"

type Mode = "speed" | "standard" | "deep"

const MODE_CONFIG: Record<Mode, { label: string; sub: string }> = {
  speed:    { label: "早足",  sub: "約30秒" },
  standard: { label: "常足",  sub: "約2分"  },
  deep:     { label: "熟考",  sub: "約5分"  },
}

const STATUS_BADGE: Record<string, { variant: "success" | "destructive" | "secondary"; label: string }> = {
  completed: { variant: "success",     label: "完了" },
  failed:    { variant: "destructive", label: "失敗" },
  pending:   { variant: "secondary",   label: "処理中" },
  running:   { variant: "secondary",   label: "熟議中" },
}

export default function DashboardClient() {
  const router = useRouter()
  const [title, setTitle]           = useState("")
  const [question, setQuestion]     = useState("")
  const [mode, setMode]             = useState<Mode>("standard")
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState("")

  const [sessions, setSessions]       = useState<SessionListItem[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(true)

  // ── STT ──
  const handleTranscript = useCallback((text: string, isFinal: boolean) => {
    if (isFinal) {
      setQuestion((prev) => (prev ? prev + " " + text : text))
    }
  }, [])
  const { status: sttStatus, supported: sttSupported, start: sttStart, stop: sttStop } = useSTT(handleTranscript)

  // ── 初回プロフィールチェック（company_name未設定 → /profile?first=1 へ誘導） ──
  useEffect(() => {
    getProfile()
      .then((profile) => {
        if (!profile.company_name) {
          router.replace("/profile?first=1")
        }
      })
      .catch(() => {
        // プロフィール未登録（404）の場合もプロフィール設定画面へ
        router.replace("/profile?first=1")
      })
  }, [router])

  // ── 過去セッション一覧ロード ──
  useEffect(() => {
    listDeliberations()
      .then(setSessions)
      .catch(() => setSessions([]))
      .finally(() => setSessionsLoading(false))
  }, [])

  // ── フォーム送信 ──
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim() || !question.trim()) return
    setSubmitting(true)
    setSubmitError("")
    try {
      const res = await createDeliberation({ title, question, mode })
      router.push(`/deliberations/${res.session_id}`)
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "送信に失敗しました")
      setSubmitting(false)
    }
  }

  const isMicListening = sttStatus === "listening"

  return (
    <div className="min-h-screen bg-background">
      <Header />

      <main className="max-w-5xl mx-auto px-4 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

          {/* ── 熟議入力フォーム ── */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">経営課題を入力</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit} className="space-y-4">

                {/* セッション名 */}
                <div className="space-y-1.5">
                  <label htmlFor="title" className="text-xs text-muted-foreground">
                    セッション名
                  </label>
                  <input
                    id="title"
                    type="text"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    required
                    maxLength={200}
                    placeholder="中期事業戦略2026"
                    className="w-full bg-input border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
                  />
                </div>

                {/* 経営課題（音声入力付き） */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label htmlFor="question" className="text-xs text-muted-foreground">
                      経営課題
                    </label>
                    {sttSupported && (
                      <Button
                        type="button"
                        variant={isMicListening ? "warning" : "ghost"}
                        size="sm"
                        className="h-7 text-xs gap-1"
                        onClick={isMicListening ? sttStop : sttStart}
                      >
                        {isMicListening ? (
                          <><MicOff className="h-3 w-3" />音声停止</>
                        ) : (
                          <><Mic className="h-3 w-3" />音声入力</>
                        )}
                      </Button>
                    )}
                  </div>
                  <textarea
                    id="question"
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    required
                    maxLength={4000}
                    rows={4}
                    placeholder="当社の5年後の主力事業をどう描くべきか？"
                    className="w-full bg-input border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring transition-colors resize-none"
                  />
                  {isMicListening && (
                    <p className="text-xs text-amber-400 flex items-center gap-1">
                      <span className="animate-pulse">●</span>
                      音声認識中…
                    </p>
                  )}
                  {sttSupported && (
                    <p className="text-xs text-muted-foreground/60">
                      ※ 音声は Google のサーバーで認識されます。機密性の高い課題はテキスト入力をご利用ください。
                    </p>
                  )}
                </div>

                {/* 熟議モード */}
                <div className="space-y-1.5">
                  <p className="text-xs text-muted-foreground">熟議モード</p>
                  <div className="grid grid-cols-3 gap-2">
                    {(Object.entries(MODE_CONFIG) as [Mode, typeof MODE_CONFIG[Mode]][]).map(([m, cfg]) => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => setMode(m)}
                        className={[
                          "rounded-lg border p-2.5 text-center text-xs transition-all",
                          mode === m
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-border text-muted-foreground hover:border-muted-foreground hover:text-foreground",
                        ].join(" ")}
                      >
                        <div className="font-semibold">{cfg.label}</div>
                        <div className="text-[10px] mt-0.5 opacity-70">{cfg.sub}</div>
                      </button>
                    ))}
                  </div>
                </div>

                {submitError && (
                  <p className="text-xs text-destructive">{submitError}</p>
                )}

                <Button
                  type="submit"
                  className="w-full"
                  size="lg"
                  disabled={submitting || !title.trim() || !question.trim()}
                >
                  {submitting ? (
                    <><Loader2 className="h-4 w-4 animate-spin" />送信中…</>
                  ) : (
                    "熟議を開始"
                  )}
                </Button>
              </form>
            </CardContent>
          </Card>

          {/* ── 過去の熟議一覧 ── */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">過去の熟議</CardTitle>
            </CardHeader>
            <CardContent>
              {sessionsLoading ? (
                <div className="flex items-center gap-2 text-muted-foreground text-sm py-4">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  読み込み中…
                </div>
              ) : sessions.length === 0 ? (
                <p className="text-sm text-muted-foreground py-4 text-center">
                  熟議履歴がありません。
                </p>
              ) : (
                <ul className="space-y-2 max-h-[480px] overflow-y-auto pr-1">
                  {sessions.map((s) => {
                    const badge = STATUS_BADGE[s.status] ?? STATUS_BADGE.pending
                    const modeLabel = MODE_CONFIG[s.mode as Mode]?.label ?? s.mode
                    const date = new Date(s.created_at).toLocaleDateString("ja-JP", {
                      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                    })
                    return (
                      <li key={s.session_id}>
                        <button
                          onClick={() => router.push(`/deliberations/${s.session_id}`)}
                          className="w-full text-left p-3 rounded-lg border border-border hover:border-primary/50 hover:bg-primary/5 transition-all group"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <p className="text-sm font-medium text-foreground group-hover:text-primary transition-colors line-clamp-1">
                              {s.title}
                            </p>
                            <Badge variant={badge.variant} className="shrink-0">
                              {badge.label}
                            </Badge>
                          </div>
                          <div className="flex items-center gap-2 mt-1">
                            <span className="text-xs text-muted-foreground">{modeLabel}</span>
                            <span className="text-muted-foreground/40 text-xs">·</span>
                            <span className="text-xs text-muted-foreground">{date}</span>
                            <span className="text-muted-foreground/40 text-xs">·</span>
                            <span className="text-xs text-muted-foreground">
                              ${s.total_cost_usd.toFixed(4)}
                            </span>
                          </div>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </CardContent>
          </Card>

        </div>
      </main>
    </div>
  )
}
