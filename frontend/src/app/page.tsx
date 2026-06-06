import { redirect } from "next/navigation"

// ルートは /dashboard にリダイレクト（認証チェックはmiddlewareが担う）
export default function RootPage() {
  redirect("/dashboard")
}
