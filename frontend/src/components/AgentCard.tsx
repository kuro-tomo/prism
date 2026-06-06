import { cn } from "@/lib/utils"

export const AGENT_META: Record<string, { label: string; color: string }> = {
  strategist: { label: "経営戦略家",    color: "#818cf8" },
  cfo:        { label: "CFO・財務",     color: "#fbbf24" },
  engineer:   { label: "技術エンジニア", color: "#34d399" },
  market:     { label: "市場アナリスト", color: "#60a5fa" },
  risk:       { label: "リスク・法務",  color: "#f87171" },
}

interface AgentCardProps {
  agentId: string
  round: number
  content: string
  populated: boolean
}

export function AgentCard({ agentId, round: _round, content, populated }: AgentCardProps) {
  const meta = AGENT_META[agentId] ?? { label: agentId, color: "#818cf8" }

  return (
    <div
      className={cn(
        "glass-card p-4 transition-all duration-300",
        populated && "agent-card-glow",
      )}
      style={{ "--agent-color": meta.color } as React.CSSProperties}
    >
      {/* エージェントラベル */}
      <div className="flex items-center gap-2 mb-2">
        <div
          className="w-2 h-2 rounded-full shrink-0"
          style={{ backgroundColor: meta.color }}
        />
        <span className="text-xs font-semibold" style={{ color: meta.color }}>
          {meta.label}
        </span>
      </div>

      {/* コンテンツ */}
      {populated ? (
        <p className="text-sm text-foreground/90 leading-relaxed whitespace-pre-wrap">
          {content}
        </p>
      ) : (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="animate-pulse">●</span>
          <span>待機中…</span>
        </div>
      )}
    </div>
  )
}
