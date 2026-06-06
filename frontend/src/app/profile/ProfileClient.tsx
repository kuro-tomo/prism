"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { Header } from "@/components/Header"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  getProfile,
  saveProfile,
  fetchProfileFromWebsite,
  type CompanyProfile,
} from "@/lib/api/client"
import { ChevronLeft, Loader2, Search, CheckCircle2 } from "lucide-react"

const INPUT_CLS =
  "w-full bg-input border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
const TEXTAREA_CLS = INPUT_CLS + " resize-vertical"

const FIELDS: {
  key: keyof CompanyProfile
  label: string
  placeholder: string
  type?: "textarea"
  rows?: number
}[] = [
  { key: "industry",        label: "業種・事業内容",   placeholder: "例：東海地区の建設機械部品メーカー" },
  { key: "scale",           label: "規模",             placeholder: "例：売上100億円・従業員300名" },
  { key: "main_products",   label: "主力製品・サービス", placeholder: "例：油圧シリンダー（国内シェア15%）" },
  { key: "main_customers",  label: "主要顧客・販売先",  placeholder: "例：国内建設機械メーカー3社" },
  { key: "strengths",       label: "現在の状況・強み",  placeholder: "例：国内シェアトップだが市場縮小傾向。設計・加工技術に強み。", type: "textarea", rows: 3 },
  { key: "challenges",      label: "主な課題",          placeholder: "例：後継者・海外展開・DX推進", type: "textarea", rows: 2 },
  { key: "goals",           label: "経営目標",          placeholder: "例：5年以内に売上150億円", type: "textarea", rows: 2 },
  { key: "custom_context",  label: "その他の文脈（自由記述）", placeholder: "例：後継者問題・親会社との関係・進行中のプロジェクトなど", type: "textarea", rows: 3 },
]

const EMPTY: CompanyProfile = {
  industry: "", scale: "", main_products: "", main_customers: "",
  strengths: "", challenges: "", goals: "", website_url: "",
  company_name: "", founded_year: null, employees: null,
  revenue_jpy: null, region: "", custom_context: "",
}

interface ProfileClientProps {
  isFirst?: boolean
}

export default function ProfileClient({ isFirst = false }: ProfileClientProps) {
  const router = useRouter()

  const [profile, setProfile]   = useState<CompanyProfile>(EMPTY)
  const [loading, setLoading]   = useState(true)
  const [saving, setSaving]     = useState(false)
  const [saved, setSaved]       = useState(false)
  const [saveError, setSaveError] = useState("")

  const [websiteUrl, setWebsiteUrl]   = useState("")
  const [fetchingUrl, setFetchingUrl] = useState(false)
  const [fetchStatus, setFetchStatus] = useState("")

  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => {
    getProfile()
      .then((p) => {
        setProfile(p)
        setWebsiteUrl(p.website_url ?? "")
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const handleChange = (key: keyof CompanyProfile, value: string) => {
    setProfile((prev) => ({ ...prev, [key]: value }))
  }

  const handleFetchFromUrl = async () => {
    if (!websiteUrl) return
    setFetchingUrl(true)
    setFetchStatus("取得中…")
    try {
      const partial = await fetchProfileFromWebsite(websiteUrl)
      setProfile((prev) => ({ ...prev, ...partial, website_url: websiteUrl }))
      setFetchStatus("✓ 自動入力完了")
    } catch {
      setFetchStatus("⚠ 取得に失敗しました")
    } finally {
      setFetchingUrl(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setSaveError("")
    try {
      await saveProfile({ ...profile, website_url: websiteUrl })
      setSaved(true)
      clearTimeout(savedTimerRef.current)
      savedTimerRef.current = setTimeout(() => setSaved(false), 3000)
      if (isFirst) router.push("/dashboard")
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "保存に失敗しました")
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-background">
        <Header />
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-6 w-6 text-primary animate-spin" />
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      <Header />

      <main className="max-w-2xl mx-auto px-4 py-8 space-y-5">
        {!isFirst && (
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground -ml-2"
            onClick={() => router.push("/dashboard")}
          >
            <ChevronLeft className="h-4 w-4" />
            ダッシュボードへ戻る
          </Button>
        )}

        <Card>
          <CardHeader>
            <CardTitle>会社プロフィール</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            {/* 初回表示 notice */}
            {isFirst && (
              <div className="glass-card border-primary/40 bg-primary/8 p-3 text-sm text-accent-foreground">
                🏢 <strong>まず会社プロフィールをご入力ください</strong><br />
                <span className="text-xs text-muted-foreground mt-0.5 block">
                  ここに入力した情報はすべての熟議に自動反映されます。前提情報が充実するほど PRISM の回答精度が大幅に向上します。
                </span>
              </div>
            )}

            {/* セキュリティ注記 */}
            <div className="glass-card border-green-600/20 bg-green-900/5 p-3 text-xs text-muted-foreground">
              🔒 <strong className="text-green-400">セキュリティについて：</strong>
              入力情報は暗号化通信（HTTPS）で送信され、専用の隔離環境に保管されます。
              第三者への提供・AI の学習データへの使用は一切行いません。
            </div>

            {/* URL 自動入力 */}
            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground font-medium">
                会社 Web サイト URL
              </label>
              <div className="flex gap-2">
                <input
                  type="url"
                  value={websiteUrl}
                  onChange={(e) => setWebsiteUrl(e.target.value)}
                  placeholder="https://www.example.co.jp/"
                  maxLength={2048}
                  className={INPUT_CLS + " flex-1"}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleFetchFromUrl}
                  disabled={fetchingUrl || !websiteUrl}
                  className="shrink-0"
                >
                  {fetchingUrl ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Search className="h-3.5 w-3.5" />
                  )}
                  URL から自動入力
                </Button>
              </div>
              {fetchStatus && (
                <p className={`text-xs ${fetchStatus.startsWith("✓") ? "text-green-400" : "text-muted-foreground"}`}>
                  {fetchStatus}
                </p>
              )}
            </div>

            {/* フォームフィールド */}
            <form onSubmit={handleSubmit} className="space-y-4">
              {FIELDS.map(({ key, label, placeholder, type, rows }) => (
                <div key={key} className="space-y-1.5">
                  <label className="text-xs text-muted-foreground font-medium">
                    {label}
                  </label>
                  {type === "textarea" ? (
                    <textarea
                      value={(profile[key] as string) ?? ""}
                      onChange={(e) => handleChange(key, e.target.value)}
                      placeholder={placeholder}
                      rows={rows ?? 2}
                      maxLength={1000}
                      className={TEXTAREA_CLS}
                    />
                  ) : (
                    <input
                      type="text"
                      value={(profile[key] as string) ?? ""}
                      onChange={(e) => handleChange(key, e.target.value)}
                      placeholder={placeholder}
                      maxLength={500}
                      className={INPUT_CLS}
                    />
                  )}
                </div>
              ))}

              {saveError && (
                <p className="text-xs text-destructive">{saveError}</p>
              )}

              {saved && (
                <div className="flex items-center gap-2 text-sm text-green-400">
                  <CheckCircle2 className="h-4 w-4" />
                  保存しました
                </div>
              )}

              <Button
                type="submit"
                className="w-full"
                size="lg"
                disabled={saving}
              >
                {saving ? (
                  <><Loader2 className="h-4 w-4 animate-spin" />保存中…</>
                ) : isFirst ? (
                  "設定して始める →"
                ) : (
                  "保存する"
                )}
              </Button>
            </form>
          </CardContent>
        </Card>
      </main>
    </div>
  )
}
