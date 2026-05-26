# 擬似フィーラ（Fira）プロジェクト

## プロジェクト概要

テクノ中部社長向け経営参謀AIシステム。  
複数の専門エージェントが熟議を行い、経営課題に「第三の解」を提示する。  
FIRAの代替品として、テクノ中部の文脈に特化して構築する。

## 技術スタック

| 層 | 技術 |
|----|------|
| AIエンジン | Anthropic Claude API（Opus：討論 / Haiku：補助） |
| バックエンド | Python 3.12 + FastAPI |
| データベース | Supabase（PostgreSQL） |
| フロントエンド | Next.js（予定） |

## ディレクトリ構成

```
fira/
├── engine/          # 熟議エンジン（コアロジック）
│   ├── agents.py    # エージェントペルソナ定義
│   ├── debate.py    # 3ラウンド熟議ループ
│   └── memory.py    # Supabase連携・記憶管理
├── api/             # FastAPI サーバー
├── frontend/        # Next.js UI（未着手）
├── tests/           # テストスイート
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
