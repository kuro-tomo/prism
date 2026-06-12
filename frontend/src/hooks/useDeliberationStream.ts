/**
 * useDeliberationStream — SSE ストリーミング フック
 * FastAPI の /deliberations/{id}/stream に EventSource で接続。
 * 仕様書: F-003/F-004/F-014 SSEストリーミング設計 §6
 *
 * SSEイベント型:
 *   session_start, round_start, agent_done, round_summary,
 *   synthesis_done, pre_mortem_done, complete, agent_error,
 *   agent_content_delta, synthesis_delta
 */
"use client"

import { useEffect, useRef, useState } from "react"
import type { ThirdSolution } from "@/lib/api/client"
import { getStreamUrl } from "@/lib/api/client"
import { AGENT_META } from "@/components/AgentCard"

// ── 型定義 ────────────────────────────────────────────────────

export type AgentId = "strategist" | "cfo" | "engineer" | "market" | "risk"
export type Round = 1 | 2

export interface AgentState {
  content: string
  populated: boolean
}

export type AgentsGrid = Record<string, Record<Round, AgentState>>

export interface StreamState {
  status: string
  currentRound: Round
  agents: AgentsGrid
  roundSummaries: Record<Round, string | null>
  synthesis: ThirdSolution | null
  synthesisDraft: string
  costUsd: number | null
  completed: boolean
  error: string | null
}

// ── フック本体 ────────────────────────────────────────────────

const AGENT_IDS = Object.keys(AGENT_META) as AgentId[]

function initialAgents(): AgentsGrid {
  const grid: AgentsGrid = {}
  for (const id of AGENT_IDS) {
    grid[id] = {
      1: { content: "", populated: false },
      2: { content: "", populated: false },
    }
  }
  return grid
}

export function useDeliberationStream(
  sessionId: string,
  /** TTS コールバック（音声読み上げ用） */
  onSpeak?: (text: string, rate?: number) => void,
) {
  const [state, setState] = useState<StreamState>({
    status: "接続中…",
    currentRound: 1,
    agents: initialAgents(),
    roundSummaries: { 1: null, 2: null },
    synthesis: null,
    synthesisDraft: "",
    costUsd: null,
    completed: false,
    error: null,
  })

  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!sessionId) return
    const url = getStreamUrl(sessionId)
    const es = new EventSource(url, { withCredentials: true })
    esRef.current = es

    const SSE_EVENTS = [
      "session_start",
      "round_start",
      "agent_done",
      "round_summary",
      "synthesis_done",
      "pre_mortem_done",
      "complete",
      "agent_error",
      "agent_content_delta",
      "synthesis_delta",
    ] as const

    function dispatch(type: string, rawData: string) {
      let data: Record<string, unknown>
      try {
        data = JSON.parse(rawData)
      } catch {
        return
      }

      switch (type) {
        case "session_start":
          setState((s) => ({ ...s, status: "熟議を開始いたします…" }))
          onSpeak?.("熟議を開始します。")
          break

        case "round_start": {
          const round = (data.round ?? 1) as Round
          setState((s) => ({
            ...s,
            status: `Round ${round} 進行中`,
            currentRound: round,
          }))
          if (round === 2) {
            onSpeak?.(`Round ${round}、反論・深掘りフェーズです。`)
          }
          break
        }

        case "agent_done": {
          const agentId = data.agent_id as string
          const round = (data.round ?? 1) as Round
          const content = (data.content ?? "") as string
          setState((s) => ({
            ...s,
            agents: {
              ...s.agents,
              [agentId]: {
                ...s.agents[agentId],
                [round]: { content, populated: true },
              },
            },
          }))
          // TTS: エージェントラベル + 冒頭120文字
          const meta = AGENT_META[agentId]
          const preview = content.substring(0, 120)
          onSpeak?.((meta ? meta.label + "。" : "") + preview, 1.05)
          break
        }

        case "round_summary": {
          const round = (data.round ?? 1) as Round
          const phi =
            typeof data.diversity_score_phi === "number"
              ? (data.diversity_score_phi as number).toFixed(2)
              : "?"
          const warn = data.consensus_risk ? " ⚠ 合意リスク検出" : ""
          setState((s) => ({
            ...s,
            roundSummaries: {
              ...s.roundSummaries,
              [round]: `多様性スコア Φ=${phi}${warn}`,
            },
          }))
          break
        }

        case "agent_content_delta": {
          const agentId = data.agent_id as string
          const round = (data.round ?? 1) as Round
          const chunk = (data.text_chunk ?? "") as string
          setState((s) => ({
            ...s,
            agents: {
              ...s.agents,
              [agentId]: {
                ...s.agents[agentId],
                [round]: {
                  content: (s.agents[agentId]?.[round]?.content ?? "") + chunk,
                  populated: false,
                },
              },
            },
          }))
          break
        }

        case "synthesis_delta": {
          const chunk = (data.text_chunk ?? "") as string
          setState((s) => ({
            ...s,
            synthesisDraft: s.synthesisDraft + chunk,
            status: "第三の解を統合中…",
          }))
          break
        }

        case "synthesis_done": {
          const synthesis = data.synthesis as ThirdSolution | null
          if (synthesis) {
            setState((s) => ({
              ...s,
              synthesis,
              synthesisDraft: "",
              status: "第三の解が完成いたしました。",
            }))
            if (synthesis.conclusion) {
              onSpeak?.("第三の解。" + synthesis.conclusion, 0.98)
            }
          }
          break
        }

        case "pre_mortem_done": {
          const scenarios = (data.failure_scenarios ?? []) as string[]
          setState((s) => ({
            ...s,
            synthesis: s.synthesis
              ? { ...s.synthesis, failure_scenarios: scenarios }
              : null,
          }))
          break
        }

        case "complete": {
          const cost = data.total_cost_usd as number | null
          setState((s) => ({
            ...s,
            costUsd: cost,
            completed: true,
            status: "熟議が完了いたしました。",
          }))
          es.close()
          onSpeak?.("熟議が完了しました。")
          break
        }

        case "agent_error": {
          const agentId = data.agent_id as string | undefined
          const errMsg = (data.error ?? "不明") as string
          setState((s) => ({
            ...s,
            status: `⚠ エラー：${agentId ? agentId + " — " : ""}${errMsg}`,
          }))
          break
        }
      }
    }

    for (const evtName of SSE_EVENTS) {
      es.addEventListener(evtName, (e: MessageEvent) => {
        dispatch(evtName, e.data)
      })
    }

    // フォールバック（名前なしメッセージ）
    es.addEventListener("message", (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data) as Record<string, unknown>
        if (typeof d.event_type === "string") dispatch(d.event_type, e.data)
      } catch {
        // 無視
      }
    })

    es.onerror = () => {
      es.close()
      setState((s) => {
        if (s.completed) return s
        return { ...s, error: "接続が切断されました。", status: "切断" }
      })
    }

    return () => {
      es.close()
    }
  }, [sessionId]) // onSpeak は参照が変わっても再接続しない

  return state
}
