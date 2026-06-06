import { notFound } from "next/navigation"
import DeliberationLiveClient from "./DeliberationLiveClient"
import DeliberationResultClient from "./DeliberationResultClient"

// FastAPI から直接取得（Cookieベース認証のためサーバー側では fetch + Cookie 転送）
async function fetchDetail(id: string, cookieHeader: string | null) {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
  const res = await fetch(`${apiUrl}/deliberations/${id}`, {
    headers: {
      ...(cookieHeader ? { Cookie: cookieHeader } : {}),
    },
    cache: "no-store",
  })
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`API Error ${res.status}`)
  return res.json()
}

interface Props {
  params: Promise<{ id: string }>
}

export default async function DeliberationPage({ params }: Props) {
  const { id } = await params

  // Next.js Server Component では cookies() でヘッダーを取得
  const { cookies } = await import("next/headers")
  const cookieStore = await cookies()
  const cookieHeader = cookieStore
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join("; ")

  const detail = await fetchDetail(id, cookieHeader).catch(() => null)
  if (!detail) notFound()

  // pending / running → ライブ画面（SSEストリーミング）
  if (detail.status === "pending" || detail.status === "running") {
    return (
      <DeliberationLiveClient
        sessionId={id}
        title={detail.title}
        question={detail.question}
        mode={detail.mode}
      />
    )
  }

  // completed / failed → 結果表示（静的）
  return <DeliberationResultClient detail={detail} />
}

export async function generateMetadata({ params }: Props) {
  const { id } = await params
  return { title: `PRISM — 熟議 ${id.substring(0, 8)}…` }
}
