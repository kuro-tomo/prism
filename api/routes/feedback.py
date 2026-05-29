"""ARIF API — /deliberations/{id}/feedback（仕様書 B-001〜B-003）"""
from __future__ import annotations

import uuid
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import User, get_current_user, get_pool
from api.schemas import FeedbackRequest, FeedbackResponse

router = APIRouter(prefix="/deliberations", tags=["feedback"])


@router.post("/{session_id}/feedback", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    session_id: UUID,
    req: FeedbackRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    user: User = Depends(get_current_user),
) -> FeedbackResponse:
    """社長フィードバックを登録する（B-001〜B-003）。"""
    async with pool.acquire() as conn:
        # セッション所有者確認
        exists = await conn.fetchval(
            "SELECT id FROM deliberation_sessions WHERE id=$1 AND user_id=$2",
            session_id, user.id,
        )
        if not exists:
            raise HTTPException(status_code=404, detail="セッションが見つかりません。")

        feedback_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO session_feedback
                (id, session_id, user_id, overall_rating, usefulness, novelty,
                 best_agent, worst_agent, free_comment, action_taken)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (session_id) DO UPDATE SET
                overall_rating=$4, usefulness=$5, novelty=$6,
                best_agent=$7, worst_agent=$8,
                free_comment=$9, action_taken=$10
            """,
            feedback_id, session_id, user.id,
            req.overall_rating, req.usefulness, req.novelty,
            req.best_agent, req.worst_agent, req.free_comment, req.action_taken,
        )

    return FeedbackResponse(feedback_id=feedback_id, session_id=session_id)
