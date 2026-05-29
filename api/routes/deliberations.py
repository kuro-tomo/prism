"""
ARIF API — /deliberations エンドポイント

仕様書 A-001〜A-004・設計書 §7 に準拠。
SSE 配信は sse-starlette の EventSourceResponse を使用（Opus 設計確定）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from api.deps import User, get_current_user, get_pool
from api.schemas import (
    AgentResponseOut,
    DeliberationDetail,
    DeliberationRequest,
    DeliberationResponse,
    SessionListItem,
)
from api.services.debate_service import run_in_background, stream_and_persist
from engine.debate import Mode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/deliberations", tags=["deliberations"])

# モード別の推定秒数
_ESTIMATED_SECONDS: dict[str, int] = {
    "speed": 30,
    "standard": 120,
    "deep": 300,
}


# ===========================
# POST /deliberations（A-001）
# ===========================

@router.post("", response_model=None, status_code=status.HTTP_202_ACCEPTED)
async def create_deliberation(
    req: DeliberationRequest,
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
    user: User = Depends(get_current_user),
) -> DeliberationResponse | JSONResponse:
    """熟議セッションを開始する。即時 202 を返し、熟議はバックグラウンドで実行。"""
    session_id = uuid.uuid4()
    # Mode は Literal エイリアスゆえ str のまま渡す（Pydantic で検証済み）
    mode: Mode = req.mode  # type: ignore[assignment]

    # セッション行を即時 INSERT（status='pending'）。
    # stream エンドポイント接続前に行が存在することを保証する（指摘H 対応）。
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO deliberation_sessions
                (id, user_id, title, question, mode, status)
            VALUES ($1, $2, $3, $4, $5, 'pending')
            ON CONFLICT (id) DO NOTHING
            """,
            session_id,
            user.id,
            req.title,
            req.question,
            req.mode,
        )

    if req.background:
        # バックグラウンド実行モード（F-013）。user.email を渡して N-002 通知を有効化
        asyncio.create_task(
            run_in_background(
                pool, session_id, user.id, req.title, req.question, mode,
                user_email=user.email,
            )
        )
    # 通常モードの場合は /stream エンドポイントで SSE 接続して実行

    result = DeliberationResponse(
        session_id=session_id,
        status="pending",
        mode=req.mode,
        estimated_seconds=_ESTIMATED_SECONDS.get(req.mode, 120),
        stream_url=f"/deliberations/{session_id}/stream",
    )

    # HTMX リクエストは全ページリダイレクト（HX-Redirect）
    if request.headers.get("HX-Request"):
        resp = JSONResponse(
            content=result.model_dump(mode="json"),
            status_code=202,
        )
        resp.headers["HX-Redirect"] = f"/deliberations/{session_id}"
        return resp

    return result


# ===========================
# GET /deliberations/{id}/stream（A-004）
# ===========================

@router.get("/{session_id}/stream")
async def stream_deliberation(
    session_id: UUID,
    request: Request,
    question: str,
    title: str,
    mode: str = "standard",
    pool: asyncpg.Pool = Depends(get_pool),
    user: User = Depends(get_current_user),
) -> EventSourceResponse:
    """
    SSE でリアルタイム熟議進行状況を配信する（仕様書 A-004・F-008・F-011）。

    クライアントは HTMX の htmx-ext-sse 拡張で受信する。
    """

    async def event_generator():
        # Mode は Literal 型ゆえ不可 instance 化。in 演算子でバリデーション（指摘E対応）。
        if mode not in ("speed", "standard", "deep"):
            yield ServerSentEvent(event="error", data=json.dumps({"message": f"不正なモード: {mode}"}))
            return
        mode_val: Mode = mode  # type: ignore[assignment]

        async for evt_name, evt_data in stream_and_persist(
            pool, session_id, user.id, title, question, mode_val
        ):
            if await request.is_disconnected():
                logger.info("クライアント切断 session_id=%s", session_id)
                break
            yield ServerSentEvent(
                event=evt_name,
                data=json.dumps(evt_data, default=str),
            )

    return EventSourceResponse(event_generator())


# ===========================
# GET /deliberations（A-002）
# ===========================

@router.get("", response_model=list[SessionListItem])
async def list_deliberations(
    pool: asyncpg.Pool = Depends(get_pool),
    user: User = Depends(get_current_user),
    limit: int = 20,
    offset: int = 0,
) -> list[SessionListItem]:
    """過去のセッション一覧を返す。"""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, mode, status, total_cost_usd, created_at
            FROM deliberation_sessions
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            user.id,
            limit,
            offset,
        )
    return [
        SessionListItem(
            session_id=r["id"],
            title=r["title"],
            mode=r["mode"],
            status=r["status"],
            total_cost_usd=float(r["total_cost_usd"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ===========================
# GET /deliberations/{id}（A-003）
# ===========================

@router.get("/{session_id}", response_model=DeliberationDetail)
async def get_deliberation(
    session_id: UUID,
    pool: asyncpg.Pool = Depends(get_pool),
    user: User = Depends(get_current_user),
) -> DeliberationDetail:
    """特定のセッション詳細（全ラウンドログ含む）を返す。"""
    async with pool.acquire() as conn:
        session_row = await conn.fetchrow(
            """
            SELECT id, title, question, mode, status,
                   third_solution, total_cost_usd, duration_seconds, created_at
            FROM deliberation_sessions
            WHERE id = $1 AND user_id = $2
            """,
            session_id,
            user.id,
        )
        if not session_row:
            raise HTTPException(status_code=404, detail="セッションが見つかりません。")

        agent_rows = await conn.fetch(
            """
            SELECT agent_id, agent_role, round, content, key_points,
                   stance, input_tokens, output_tokens, latency_ms
            FROM agent_responses
            WHERE session_id = $1
            ORDER BY round, created_at
            """,
            session_id,
        )

    third_solution = None
    if session_row["third_solution"]:
        raw = session_row["third_solution"]
        third_solution = json.loads(raw) if isinstance(raw, str) else raw

    return DeliberationDetail(
        session_id=session_row["id"],
        title=session_row["title"],
        question=session_row["question"],
        mode=session_row["mode"],
        status=session_row["status"],
        third_solution=third_solution,
        total_cost_usd=float(session_row["total_cost_usd"]),
        duration_seconds=session_row["duration_seconds"],
        created_at=session_row["created_at"],
        agent_responses=[
            AgentResponseOut(
                agent_id=r["agent_id"],
                agent_role=r["agent_role"],
                round=r["round"],
                content=r["content"],
                key_points=json.loads(r["key_points"]) if isinstance(r["key_points"], str) else r["key_points"] or [],
                stance=r["stance"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                latency_ms=r["latency_ms"],
            )
            for r in agent_rows
        ],
    )
