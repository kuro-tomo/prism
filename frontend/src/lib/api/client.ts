/**
 * FastAPI クライアント
 * Next.js rewrites で /api/* → FastAPI にプロキシ。Cookie認証は自動で透過する。
 * 仕様書: API設計 §7 / Next.js移行設計 §3
 */

const BASE = "/api"

// ── 型定義 ──────────────────────────────────────────────────────

export interface DeliberationRequest {
  question: string
  title: string
  mode: "speed" | "standard" | "deep"
  include_philosopher?: boolean
  background?: boolean
}

export interface DeliberationResponse {
  session_id: string
  status: string
  mode: string
  estimated_seconds: number
  stream_url: string
}

export interface AgentResponseOut {
  agent_id: string
  agent_role: string
  round: number
  content: string
  key_points: Record<string, unknown>[]
  stance: string | null
  input_tokens: number | null
  output_tokens: number | null
  latency_ms: number | null
}

export interface ThirdSolution {
  conclusion: string
  rationale: { agent: string; point: string }[]
  actions_short_term: string[]
  actions_mid_term: string[]
  minority_view?: string
  guilford_scores?: {
    fluency: number
    flexibility: number
    elaboration: number
    originality: number
  }
  assumptions?: string[]
  failure_scenarios?: string[]
  disclaimer?: string
}

export interface DeliberationDetail {
  session_id: string
  title: string
  question: string
  mode: string
  status: string
  third_solution: ThirdSolution | null
  total_cost_usd: number
  duration_seconds: number | null
  created_at: string
  agent_responses: AgentResponseOut[]
}

export interface SessionListItem {
  session_id: string
  title: string
  mode: string
  status: string
  total_cost_usd: number
  created_at: string
}

export interface CompanyProfile {
  industry: string
  scale: string
  main_products: string
  main_customers: string
  strengths: string
  challenges: string
  goals: string
  website_url: string
  company_name: string
  founded_year: number | null
  employees: number | null
  revenue_jpy: number | null
  region: string
  custom_context: string
}

// ── 共通フェッチ ──────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    credentials: "include", // Cookie を必ず送信
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {}),
    },
  })

  if (!res.ok) {
    const text = await res.text().catch(() => "")
    throw new Error(`${res.status}: ${text || res.statusText}`)
  }
  return res.json() as Promise<T>
}

// ── 熟議 API ──────────────────────────────────────────────────

export async function createDeliberation(
  req: DeliberationRequest,
): Promise<DeliberationResponse> {
  return apiFetch<DeliberationResponse>("/deliberations", {
    method: "POST",
    body: JSON.stringify(req),
  })
}

export async function listDeliberations(): Promise<SessionListItem[]> {
  return apiFetch<SessionListItem[]>("/deliberations")
}

export async function getDeliberation(id: string): Promise<DeliberationDetail> {
  return apiFetch<DeliberationDetail>(`/deliberations/${id}`)
}

// SSE Stream URL（EventSource に直接渡す。プロキシ経由）
export function getStreamUrl(sessionId: string): string {
  return `${BASE}/deliberations/${sessionId}/stream`
}

// ── プロフィール API ──────────────────────────────────────────

export async function getProfile(): Promise<CompanyProfile> {
  return apiFetch<CompanyProfile>("/profile/json")
}

export async function saveProfile(profile: Partial<CompanyProfile>): Promise<void> {
  // FastAPI はフォームとして受け取る
  const form = new URLSearchParams()
  Object.entries(profile).forEach(([k, v]) => {
    if (v !== null && v !== undefined) form.set(k, String(v))
  })
  const res = await fetch(`${BASE}/profile`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  })
  if (!res.ok) throw new Error(`${res.status}: ${res.statusText}`)
}

export async function fetchProfileFromWebsite(url: string): Promise<Partial<CompanyProfile>> {
  return apiFetch<Partial<CompanyProfile>>("/profile/fetch", {
    method: "POST",
    body: JSON.stringify({ url }),
  })
}

// ── 認証 API ──────────────────────────────────────────────────

export async function sendMagicLink(email: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>("/auth/magic-link", {
    method: "POST",
    body: JSON.stringify({ email }),
  })
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/auth/logout`, {
    method: "POST",
    credentials: "include",
  })
}
