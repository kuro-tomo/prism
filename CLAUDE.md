# PRISM プロジェクト

## プロジェクト概要

テクノ中部社長向け経営参謀AIシステム。
複数の専門エージェントが熟議を行い、経営課題に「第三の解」を提示する。
FIRAの代替品として、テクノ中部の文脈に特化して構築する。

正式名称：PRISM（Parallel Reasoning Intelligence System for Management）
内部コードネーム：arif（スキーマ名・環境変数 `ARIF_*` は据え置き）

## 現在状態（2026-06-10時点）

- Phase 0〜5 完了（本番稼働中）/ テスト209件全通過・型エラーゼロ
- 仕様書 v0.16 / 設計書 v0.20
- 本番URL：https://prism-lz5d.onrender.com（Render Free・oregon）
- embedding：Voyage AI voyage-3（1024次元）
- 認証：Supabase Auth + Magic Link
- FIRAデモ：2026-06-15（テクノ中部）

## 技術スタック

| 層 | 技術 |
|----|------|
| AIエンジン | Anthropic Claude API（Opus 4.6：討論 / Haiku 4.5：補助） |
| バックエンド | Python 3.12 + FastAPI |
| データベース | Supabase（PostgreSQL + pgvector） |
| フロントエンド（HTMX） | HTMX + Jinja2テンプレート（Phase 3〜5完成済み） |
| フロントエンド（Next.js） | Next.js 16（App Router）+ shadcn/ui（2026-06-06追加） |
| embedding | Voyage AI voyage-3 |

## ディレクトリ構成

```
prism/
├── engine/          # 熟議エンジン（コアロジック）
│   ├── agents.py    # エージェントペルソナ定義
│   ├── debate.py    # 3ラウンド熟議ループ
│   ├── memory.py    # Supabase連携・記憶管理
│   ├── pricing.py   # トークンコスト計算
│   └── web_fetch.py # Webサイト自動読み込み（SSRF防御）
├── api/             # FastAPI サーバー
│   ├── routes/      # エンドポイント群
│   ├── services/    # ビジネスロジック
│   └── templates/   # HTMXテンプレート
├── frontend/        # Next.js 16 フロントエンド（App Router）
│   └── src/
│       ├── app/     # 全6画面（login/dashboard/deliberations/profile/auth/callback）
│       ├── hooks/   # useDeliberationStream / useTTS / useSTT
│       └── components/ # AgentCard / Header / SynthesisPanel
├── tests/           # テストスイート（209件）
└── docs/            # 仕様書・設計書
```

## 開発作法

- グローバルの CLAUDE.md（鍛冶体制）に従うこと
- 核心ロジック（熟議ループ・プロンプト設計）は `/model opus` で実装
- エージェントペルソナは仕様書と必ず同期させること
- 1回の熟議コストを $0.20 以内に保つこと

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # APIキーを設定
```

## テスト実行

```bash
pytest tests/
```

## 主要ドキュメント

- [仕様書](docs/仕様書.md)
- [設計書](docs/設計書.md)
