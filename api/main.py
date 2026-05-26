"""
FastAPI サーバー — エントリポイント

エンドポイント（設計諮問後に確定）:
  POST /debate        課題を受け取り熟議を実行
  GET  /debates       過去の議論一覧を返す
  GET  /debates/{id}  特定の議論詳細を返す

TODO（/advisor 諮問後に実装）
"""

from fastapi import FastAPI

app = FastAPI(title="ARIF API", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
