import { useState, useEffect } from "react"
import type { ThirdSolution } from "@/lib/api/client"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { AGENT_META } from "@/components/AgentCard"

interface SynthesisPanelProps {
  synthesis: ThirdSolution
  sessionId?: string
}

function useActionChecklist(sessionId: string | undefined, count: number, prefix: string) {
  const key = sessionId ? `prism:checklist:${sessionId}:${prefix}` : null
  const [checked, setChecked] = useState<boolean[]>(() => Array(count).fill(false))

  useEffect(() => {
    if (!key) return
    try {
      const stored = localStorage.getItem(key)
      if (stored) {
        const parsed: boolean[] = JSON.parse(stored)
        // count と長さが異なる場合（項目数変更）は count に合わせてパディング・切り詰め
        const adjusted = Array(count).fill(false).map((_, i) => parsed[i] ?? false)
        setChecked(adjusted)
      }
    } catch { /* ignore */ }
  }, [key, count])

  const toggle = (i: number) => {
    setChecked(prev => {
      const next = [...prev]
      next[i] = !next[i]
      if (key) {
        try { localStorage.setItem(key, JSON.stringify(next)) } catch { /* ignore */ }
      }
      return next
    })
  }

  return { checked, toggle }
}

export function SynthesisPanel({ synthesis, sessionId }: SynthesisPanelProps) {
  const disclaimer = synthesis.disclaimer || "PRISMの分析に基づく提案です。"
  const shortCount = synthesis.actions_short_term?.length ?? 0
  const midCount = synthesis.actions_mid_term?.length ?? 0
  const { checked: shortChecked, toggle: toggleShort } = useActionChecklist(sessionId, shortCount, "short")
  const { checked: midChecked, toggle: toggleMid } = useActionChecklist(sessionId, midCount, "mid")

  return (
    <div className="space-y-4">
      {/* 完了バナー */}
      <div className="glass-card p-4 border border-primary/30 bg-primary/5">
        <span className="text-sm font-semibold text-primary">✅ 熟議完了</span>
      </div>

      {/* 第三の解 */}
      <Card>
        <CardHeader className="pb-3">
          <div className="text-xs font-semibold text-primary uppercase tracking-wider mb-1">
            第三の解
          </div>
          <p className="text-base font-semibold text-foreground leading-relaxed">
            {synthesis.conclusion}
          </p>
        </CardHeader>

        <CardContent className="space-y-4">
          {/* 論拠 */}
          {synthesis.rationale?.length > 0 && (
            <section>
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">論拠</h4>
              <div className="space-y-2">
                {synthesis.rationale.map((r, i) => (
                  <div key={i} className="flex gap-2 text-sm">
                    <span className="text-primary shrink-0">{AGENT_META[r.agent]?.label ?? r.agent}</span>
                    <span className="text-foreground/80">{r.point}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* 短期アクション */}
          {synthesis.actions_short_term?.length > 0 && (
            <section>
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                短期アクション（即実行）
              </h4>
              <ul className="space-y-2">
                {synthesis.actions_short_term.map((a, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <button
                      type="button"
                      onClick={() => toggleShort(i)}
                      className="mt-0.5 shrink-0 w-4 h-4 rounded border border-primary/50 flex items-center justify-center hover:bg-primary/10 transition-colors"
                      aria-label={shortChecked[i] ? "未完了に戻す" : "完了にする"}
                    >
                      {shortChecked[i] && <span className="text-primary text-xs leading-none">✓</span>}
                    </button>
                    <span className={shortChecked[i] ? "line-through text-muted-foreground" : "text-foreground/80"}>{a}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* 中期ロードマップ */}
          {synthesis.actions_mid_term?.length > 0 && (
            <section>
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                中期ロードマップ
              </h4>
              <ul className="space-y-2">
                {synthesis.actions_mid_term.map((a, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <button
                      type="button"
                      onClick={() => toggleMid(i)}
                      className="mt-0.5 shrink-0 w-4 h-4 rounded border border-muted-foreground/40 flex items-center justify-center hover:bg-muted/20 transition-colors"
                      aria-label={midChecked[i] ? "未完了に戻す" : "完了にする"}
                    >
                      {midChecked[i] && <span className="text-muted-foreground text-xs leading-none">✓</span>}
                    </button>
                    <span className={midChecked[i] ? "line-through text-muted-foreground" : "text-foreground/80"}>{a}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* 少数意見 */}
          {synthesis.minority_view && (
            <section>
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">少数意見</h4>
              <p className="text-sm text-muted-foreground italic">{synthesis.minority_view}</p>
            </section>
          )}

          {/* 回答品質（Guilford 平均→★表示） */}
          {synthesis.guilford_scores && (() => {
            const s = synthesis.guilford_scores!
            const avg = (s.fluency + s.flexibility + s.elaboration + s.originality) / 4
            const stars = Math.round(avg)
            const tooltip = `流暢性:${s.fluency} / 柔軟性:${s.flexibility} / 精緻性:${s.elaboration} / 独自性:${s.originality}`
            return (
              <section>
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                  回答品質
                </h4>
                <div
                  className="flex items-center gap-2 cursor-help"
                  title={tooltip}
                >
                  <span className="text-xl text-primary leading-none">
                    {"★".repeat(stars)}{"☆".repeat(5 - stars)}
                  </span>
                  <span className="text-xs text-muted-foreground">{avg.toFixed(1)} / 5</span>
                </div>
              </section>
            )
          })()}

          {/* 主要前提 */}
          {(synthesis.assumptions?.length ?? 0) > 0 && (
            <section>
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">主要前提</h4>
              <ul className="space-y-1">
                {synthesis.assumptions?.map((a, i) => (
                  <li key={i} className="text-sm text-muted-foreground flex gap-2">
                    <span>・</span><span>{a}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* 免責文言（F-015: 固定・改変禁止） */}
          <div className="border-t border-border pt-3">
            <p className="text-xs text-muted-foreground italic">{disclaimer}</p>
          </div>
        </CardContent>
      </Card>

      {/* Pre-mortem */}
      {(synthesis.failure_scenarios?.length ?? 0) > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">失敗シナリオ予測</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1">
              {synthesis.failure_scenarios?.map((s, i) => (
                <li key={i} className="flex gap-2 text-sm text-red-400/80">
                  <span className="shrink-0">⚠</span>
                  <span>{typeof s === "string" ? s : JSON.stringify(s)}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
