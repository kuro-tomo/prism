"use client"

import { useRouter } from "next/navigation"
import { Header } from "@/components/Header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { SynthesisPanel } from "@/components/SynthesisPanel"
import type { DeliberationDetail } from "@/lib/api/client"
import { ChevronLeft } from "lucide-react"

const MODE_LABELS: Record<string, string> = {
  speed: "早足", standard: "常足", deep: "熟考",
}

interface Props {
  detail: DeliberationDetail
}

export default function DeliberationResultClient({ detail }: Props) {
  const router = useRouter()

  const date = new Date(detail.created_at).toLocaleString("ja-JP", {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  })

  return (
    <div className="min-h-screen bg-background">
      <Header />

      <main className="max-w-4xl mx-auto px-4 py-6 space-y-5">

        <div>
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground -ml-2"
            onClick={() => router.push("/dashboard")}
          >
            <ChevronLeft className="h-4 w-4" />
            ダッシュボードへ戻る
          </Button>
        </div>

        {/* ── ヘッダー情報 ── */}
        <div className="space-y-1">
          <h2 className="text-xl font-bold text-foreground">{detail.title}</h2>
          <p className="text-sm text-muted-foreground">「{detail.question}」</p>
          <div className="flex items-center gap-2 flex-wrap mt-1">
            <Badge variant="secondary">{MODE_LABELS[detail.mode] ?? detail.mode}</Badge>
            {detail.status === "completed" ? (
              <Badge variant="success">完了</Badge>
            ) : (
              <Badge variant="destructive">失敗</Badge>
            )}
            <span className="text-xs text-muted-foreground">{date}</span>
            {detail.duration_seconds && (
              <span className="text-xs text-muted-foreground">{detail.duration_seconds}秒</span>
            )}
          </div>
        </div>

        {/* ── 失敗時 ── */}
        {detail.status === "failed" && (
          <Card>
            <CardContent className="py-4">
              <p className="text-sm text-destructive">
                この熟議セッションは失敗しました。新規に熟議を開始してください。
              </p>
            </CardContent>
          </Card>
        )}

        {/* ── 第三の解 ── */}
        {detail.third_solution ? (
          <SynthesisPanel
            synthesis={detail.third_solution}
          />
        ) : (
          detail.status !== "failed" && (
            <Card>
              <CardContent className="py-4">
                <p className="text-sm text-muted-foreground">第三の解が見つかりませんでした。</p>
              </CardContent>
            </Card>
          )
        )}
      </main>
    </div>
  )
}
