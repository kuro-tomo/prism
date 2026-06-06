import type { Metadata } from "next"
import "./globals.css"

export const metadata: Metadata = {
  title: "PRISM — 経営参謀AI",
  description:
    "Parallel Reasoning Intelligence System for Management — 複数専門エージェントが熟議し、経営課題に「第三の解」を提示する経営参謀AI",
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="ja">
      <body className="min-h-screen bg-background text-foreground antialiased">
        {children}
      </body>
    </html>
  )
}
