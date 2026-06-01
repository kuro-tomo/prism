"""
ARIF API — FastAPI エントリポイント

Opus 設計確定：
  - lifespan で DB プールを初期化・cleanup
  - 起動時に orphan session を recovery
  - routes/ を prefix 付きで登録
  - static/ と templates/ を提供
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routes import auth as auth_router
from api.routes import context as context_router
from api.routes import deliberations as deliberations_router
from api.routes import feedback as feedback_router
from api.routes import pages as pages_router
from api.routes import profile as profile_router
from api.schemas import HealthResponse
from api.services.debate_service import recover_orphan_sessions
from engine.memory import get_db_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ===========================
# lifespan（起動・終了処理）
# ===========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    起動時：DB プール初期化 + orphan session recovery
    終了時：DB プール解放
    """
    logger.info("ARIF API 起動中 —— DB プールを初期化しています")
    try:
        pool = await get_db_pool()
        app.state.pool = pool
        recovered = await recover_orphan_sessions(pool)
        if recovered > 0:
            logger.warning("起動 recovery: %d 件のセッションを failed に更新", recovered)
        logger.info("ARIF API 起動完了（DB 接続済み）")
    except Exception as exc:
        # DB 接続失敗でもアプリは起動させる（/health が 200 を返し Render の起動チェックを通す）
        # 各リクエスト処理時に app.state.pool が存在しない場合は適切にエラーを返す
        logger.error("DB プール初期化失敗（起動は継続）: %s", exc)
        app.state.pool = None

    logger.info("ARIF API 起動完了")
    yield

    pool = getattr(app.state, "pool", None)
    if pool:
        await pool.close()
    logger.info("ARIF API 終了 —— DB プールをクローズしました")


# ===========================
# FastAPI app
# ===========================

app = FastAPI(
    title="ARIF API",
    description="経営参謀AI — 5体エージェント×3ラウンド熟議→第三の解",
    version="0.9.0",
    lifespan=lifespan,
)

# static ファイル（htmx.min.js・htmx-ext-sse.min.js・style.css）
_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ===========================
# ルーター登録
# ===========================

app.include_router(pages_router.router)                        # HTML ページ（/, /login, /deliberations/{id}）
app.include_router(auth_router.router)                         # /auth/magic-link, /auth/callback, /auth/logout
app.include_router(deliberations_router.router)                # /deliberations
app.include_router(feedback_router.router)                     # /deliberations/{id}/feedback
app.include_router(context_router.router)                      # /context
app.include_router(profile_router.router)                      # /profile（Phase 5T: 会社プロフィール）


# ===========================
# ヘルスチェック
# ===========================

@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
