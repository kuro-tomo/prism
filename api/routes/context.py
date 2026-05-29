"""ARIF API — /context（会社コンテキスト管理・仕様書 M-003・M-004）"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import User, get_current_user, get_pool
from api.schemas import ContextEntryRequest, ContextEntryResponse, ContextListItem
from engine.memory import embed_text

logger = logging.getLogger(__name__)

# CPython GC によるタスク途中消失を防ぐ強参照セット（create_task の定石）
# https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
_background_tasks: set[asyncio.Task[None]] = set()

router = APIRouter(prefix="/context", tags=["context"])


@router.get("", response_model=list[ContextListItem])
async def list_context(
    pool: asyncpg.Pool = Depends(get_pool),
    user: User = Depends(get_current_user),
    category: str | None = None,
) -> list[ContextListItem]:
    """会社コンテキスト一覧を返す。"""
    async with pool.acquire() as conn:
        if category:
            rows = await conn.fetch(
                """
                SELECT id, category, title, source, valid_from, valid_until, created_at
                FROM company_context WHERE user_id=$1 AND category=$2
                ORDER BY created_at DESC
                """,
                user.id, category,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, category, title, source, valid_from, valid_until, created_at
                FROM company_context WHERE user_id=$1
                ORDER BY created_at DESC
                """,
                user.id,
            )
    return [
        ContextListItem(
            context_id=r["id"],
            category=r["category"],
            title=r["title"],
            source=r["source"],
            valid_from=r["valid_from"],
            valid_until=r["valid_until"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("", response_model=ContextEntryResponse, status_code=status.HTTP_201_CREATED)
async def create_context(
    req: ContextEntryRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    user: User = Depends(get_current_user),
) -> ContextEntryResponse:
    """会社コンテキストを登録する（embedding は非同期で後処理・M-004）。"""
    context_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO company_context
                (id, user_id, category, title, content, source, valid_from, valid_until)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            context_id, user.id,
            req.category, req.title, req.content,
            req.source, req.valid_from, req.valid_until,
        )
    # Phase 5T: embedding を非同期バックグラウンドで生成して DB に書き込む
    async def _generate_embedding(cid: UUID, text: str) -> None:
        try:
            embedding = await embed_text(text)
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE company_context SET embedding=$1::vector WHERE id=$2",
                    embedding_str,
                    cid,
                )
        except Exception:
            logger.exception("embedding 生成失敗 context_id=%s", cid)

    task = asyncio.create_task(_generate_embedding(context_id, f"{req.title} {req.content}"))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)  # 完了後に自動破棄

    return ContextEntryResponse(
        context_id=context_id,
        category=req.category,
        title=req.title,
        created_at=datetime.now(timezone.utc),
    )
