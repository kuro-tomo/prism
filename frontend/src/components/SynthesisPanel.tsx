import type { ThirdSolution } from "@/lib/api/client"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface SynthesisPanelProps {
  synthesis: ThirdSolution
  costUsd?: number
}

export function SynthesisPanel({ synthesis, costUsd }: SynthesisPanelProps) {
  const disclaimer =
    synthesis.disclaimer ||
    "本提案は PRISM が熟議を経て導出した最善の一手です。最終判断は社長が下されるものです。"

  return (
    <div className="space-y-4">
      {/* 完了バナー */}
      <div className="glass-card p-4 border border-primary/30 bg-primary/5 flex items-center justify-between">
        <span className="text-sm font-semibold text-primary">✅ 熟議完了</span>
        {costUsd !== undefined && (
          <span className="text-xs text-muted-foreground">
            コスト ${costUsd.toFixed(4)}
          </span>
        )}
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
                    <span className="text-primary shrink-0">{r.agent}</span>
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
              <ul className="space-y-1">
                {synthesis.actions_short_term.map((a, i) => (
                  <li key={i} className="flex gap-2 text-sm text-foreground/80">
                    <span className="text-primary shrink-0">▶</span>
                    <span>{a}</span>
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
              <ul className="space-y-1">
                {synthesis.actions_mid_term.map((a, i) => (
                  <li key={i} className="flex gap-2 text-sm text-foreground/80">
                    <span className="text-muted-foreground shrink-0">→</span>
                    <span>{a}</span>
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

          {/* Guilford スコア */}
          {synthesis.guilford_scores && (
            <section>
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                創造性評価（Guilford）
              </h4>
              <div className="grid grid-cols-4 gap-2">
                {(
                  [
                    ["fluency",      "流暢性"],
                    ["flexibility",  "柔軟性"],
                    ["elaboration",  "精緻性"],
                    ["originality",  "独自性"],
                  ] as const
                ).map(([key, label]) => (
                  <div key={key} className="text-center glass-card p-2">
                    <div className="text-lg font-bold text-primary">
                      {synthesis.guilford_scores![key] ?? "-"}/5
                    </div>
                    <div className="text-xs text-muted-foreground">{label}</div>
                  </div>
                ))}
              </div>
            </section>
          )}

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
            <CardTitle className="text-sm">失敗シナリオ（Pre-mortem）</CardTitle>
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
