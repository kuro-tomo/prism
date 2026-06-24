"use client"

import { useRouter } from "next/navigation"
import { Header } from "@/components/Header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { AgentCard, AGENT_META } from "@/components/AgentCard"
import { SynthesisPanel } from "@/components/SynthesisPanel"
import { useDeliberationStream } from "@/hooks/useDeliberationStream"
import { useTTS } from "@/hooks/useTTS"
import { ChevronLeft, Volume2, VolumeX } from "lucide-react"

const MODE_LABELS: Record<string, string> = {
  speed: "早足", standard: "常足", deep: "熟考",
}

const AGENT_IDS = Object.keys(AGENT_META)

interface Props {
  sessionId: string
  title: string
  question: string
  mode: string
}

export default function DeliberationLiveClient({ sessionId, title, question, mode }: Props) {
  const router = useRouter()
  const { voiceOn, supported: ttsSupported, speak, toggle: toggleVoice } = useTTS()

  const streamState = useDeliberationStream(sessionId, speak)

  const {
    status,
    currentRound,
    agents,
    roundSummaries,
    synthesis,
    synthesisDraft,
    costUsd: _costUsd,
    completed,
    error,
  } = streamState

  return (
    <div className="min-h-screen bg-background">
      <Header />

      <main className="max-w-5xl mx-auto px-4 py-6 space-y-6">

        {/* ── ヘッダー情報 ── */}
        <div className="space-y-2">
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground -ml-2"
            onClick={() => router.push("/dashboard")}
          >
            <ChevronLeft className="h-4 w-4" />
            ダッシュボードへ戻る
          </Button>

          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <h2 className="text-xl font-bold text-foreground">{title}</h2>
              <p className="text-sm text-muted-foreground mt-0.5">「{question}」</p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Badge variant="secondary">{MODE_LABELS[mode] ?? mode}</Badge>
              {ttsSupported && (
                <Button
                  variant={voiceOn ? "default" : "ghost"}
                  size="sm"
                  onClick={toggleVoice}
                  className="text-xs gap-1"
                  title="音声読み上げ ON/OFF"
                >
                  {voiceOn ? (
                    <><Volume2 className="h-3.5 w-3.5" />音声ON</>
                  ) : (
                    <><VolumeX className="h-3.5 w-3.5" />音声OFF</>
                  )}
                </Button>
              )}
            </div>
          </div>

          {/* ステータスバー */}
          <div className="glass-card px-4 py-2 text-sm text-muted-foreground">
            {error ? (
              <span className="text-red-400">{error}</span>
            ) : (
              status
            )}
          </div>
        </div>

        {/* ── 第三の解（常時先頭表示） ── */}
        {error && !synthesis ? (
          <Card>
            <CardContent className="py-4">
              <p className="text-sm text-destructive">{error}</p>
            </CardContent>
          </Card>
        ) : synthesis ? (
          <SynthesisPanel synthesis={synthesis} sessionId={sessionId} />
        ) : synthesisDraft ? (
          <Card>
            <CardHeader className="pb-3">
              <div className="text-xs font-semibold text-primary uppercase tracking-wider">
                第三の解（統合中）
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-foreground/90 leading-relaxed whitespace-pre-wrap">
                {synthesisDraft}
              </p>
              <div className="flex items-center gap-2 mt-3 text-xs text-muted-foreground">
                <span className="animate-pulse text-primary">●</span>
                統合中…
              </div>
            </CardContent>
          </Card>
        ) : (
          !completed && !error && (
            <Card>
              <CardContent className="py-6 flex items-center justify-center gap-3 text-muted-foreground">
                <div className="flex gap-1">
                  {["●", "●", "●"].map((dot, i) => (
                    <span
                      key={i}
                      className="text-primary/60 animate-pulse"
                      style={{ animationDelay: `${i * 0.2}s` }}
                    >
                      {dot}
                    </span>
                  ))}
                </div>
                <span className="text-sm">熟議進行中…</span>
              </CardContent>
            </Card>
          )
        )}

        {/* ── 熟議過程（折り畳み） ── */}
        <details className="group">
          <summary className="cursor-pointer select-none list-none flex items-center gap-2 py-2 text-xs font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition-colors">
            <span className="transition-transform duration-200 group-open:rotate-90 inline-block">▶</span>
            熟議過程
          </summary>

          <div className="mt-4 space-y-6">
            {/* ── Round 1 ── */}
            <section>
              <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">
                Round 1 — 独立意見
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {AGENT_IDS.map((id) => (
                  <AgentCard
                    key={`${id}-r1`}
                    agentId={id}
                    round={1}
                    content={agents[id]?.[1]?.content ?? ""}
                    populated={agents[id]?.[1]?.populated ?? false}
                  />
                ))}
              </div>
              {roundSummaries[1] && (
                <div className="mt-3 glass-card px-4 py-2 text-xs text-muted-foreground">
                  {roundSummaries[1]}
                </div>
              )}
            </section>

            {/* ── Round 2（Round 2 開始後に表示） ── */}
            {currentRound >= 2 && (
              <section>
                <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">
                  Round 2 — 反論・深掘り
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {AGENT_IDS.map((id) => (
                    <AgentCard
                      key={`${id}-r2`}
                      agentId={id}
                      round={2}
                      content={agents[id]?.[2]?.content ?? ""}
                      populated={agents[id]?.[2]?.populated ?? false}
                    />
                  ))}
                </div>
                {roundSummaries[2] && (
                  <div className="mt-3 glass-card px-4 py-2 text-xs text-muted-foreground">
                    {roundSummaries[2]}
                  </div>
                )}
              </section>
            )}
          </div>
        </details>

      </main>
    </div>
  )
}
