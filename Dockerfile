# =============================================================
# PRISM — Dockerfile
# Runtime: Python 3.11-slim（ローカル開発環境と揃える）
# Deploy target: Render（Web Service・Free Plan）
# =============================================================

FROM python:3.11-slim

WORKDIR /app

# asyncpg / psycopg2 のビルドに必要なシステムライブラリ
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 依存パッケージをキャッシュ層で先にインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコード
COPY . .

# ポート宣言（自己文書化）
# 実際のバインドポートは Render の PORT 環境変数が上書きする
EXPOSE 8000

# Render は PORT 環境変数を自動設定する（デフォルト 10000）
# 0.0.0.0 バインド必須（コンテナ外からのアクセスのため）
# --workers 1 固定：
#   ① Free Plan は 512MB RAM / 0.5 CPU でありマルチワーカーはメモリ逼迫を招く
#   ② asyncio.create_task による背景タスク（embedding生成・orphan recovery）が
#      マルチワーカー構成では状態共有できず二重実行やデータ競合を起こす
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
