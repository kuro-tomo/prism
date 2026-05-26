# ARIF（アリフ）

> テクノ中部専用 経営参謀AI ── 複数の専門エージェントが熟議し、「第三の解」を提示する

## ステータス

🟡 設計諮問中（骨格のみ。実装は /advisor 諮問後）

## 概要

日立・ハピネスプラネット社の「FIRA」と同等の機能を、  
テクノ中部の経営課題に特化して自社構築したシステム。

## セットアップ

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env に ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_KEY を設定
uvicorn api.main:app --reload
```
