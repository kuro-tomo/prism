"""
ARIF — debate_service

stream_debate() async generator と DB 段階書き込みを結合する fan-out レイヤー。
Opus 設計諮問確定：generator 内で DB 書き込み → SSE 配信を順次処理。

イベント別書き込みタイミング（設計書 §6.2）：
  session_start   → sessions INSERT（status='round1'）
  agent_done      → agent_responses INSERT
  round_summary   → sessions UPDATE（status='round2' or 'synthesis'）
  synthesis_done  → sessions UPDATE（third_solution=JSONB）
  pre_mortem_done → sessions UPDATE（third_solution に failure_scenarios をマージ）
  complete        → sessions UPDATE（status='completed'・duration）
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import asyncpg

from engine.agents import AGENTS, DEBATE_MODEL, SUMMARY_MODEL
from engine.debate import (
    AgentContentDeltaEvent,
    AgentDoneEvent,
    AgentErrorEvent,
    AgentThinkingDeltaEvent,
    CompleteEvent,
    DebateEvent,
    Mode,
    PreMortemDoneEvent,
    RoundStartEvent,
    RoundSummaryEvent,
    SessionStartEvent,
    SynthesisDeltaEvent,
    SynthesisDoneEvent,
    stream_debate,
)
from engine.memory import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PRE_MORTEM,
    STATUS_ROUND2,
    STATUS_SYNTHESIS,
    build_context_prompt,
)
from api.services.notification import notify_completion

logger = logging.getLogger(__name__)

# モード別の討論モデル（_MODE_CONFIG のミラー・追加インポートなしで解決）
_MODE_DEBATE_MODELS: dict[str, str] = {
    "speed":    SUMMARY_MODEL,  # Haiku
    "standard": DEBATE_MODEL,   # Opus
    "deep":     DEBATE_MODEL,   # Opus
}

# ===========================
# イベント名変換
# ===========================

_EVENT_NAME_MAP: dict[type, str] = {
    SessionStartEvent:       "session_start",
    RoundStartEvent:         "round_start",
    AgentDoneEvent:          "agent_done",
    AgentContentDeltaEvent:  "agent_content_delta",   # Phase 3.5
    AgentThinkingDeltaEvent: "agent_thinking_delta",  # Phase 3.5（deep モード）
    SynthesisDeltaEvent:     "synthesis_delta",        # Phase 3.5
    AgentErrorEvent:         "agent_error",            # Phase 3.5
    RoundSummaryEvent:       "round_summary",
    SynthesisDoneEvent:      "synthesis_done",
    PreMortemDoneEvent:      "pre_mortem_done",
    CompleteEvent:           "complete",
}


def event_name(event: DebateEvent) -> str:
    return _EVENT_NAME_MAP.get(type(event), "unknown")


def _event_to_dict(event: DebateEvent) -> dict[str, Any]:
    """dataclass を JSON シリアライズ可能な dict に変換。"""
    return dataclasses.asdict(event)  # type: ignore[arg-type]


# ===========================
# DB 書き込みヘルパー
# ===========================

async def _persist_session_start(
    pool: asyncpg.Pool,
    session_id: UUID,
    user_id: str,
    title: str,
    question: str,
    mode: str,
    event: SessionStartEvent,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO deliberation_sessions
                (id, user_id, title, question, mode, status)
            VALUES ($1, $2, $3, $4, $5, 'round1')
            ON CONFLICT (id) DO NOTHING
            """,
            session_id,
            user_id,
            title,
            question,
            mode,
        )


async def _persist_agent_done(
    pool: asyncpg.Pool,
    session_id: UUID,
    event: AgentDoneEvent,
    agent_role: str,
    model_used: str,
) -> None:
    """
    AgentDoneEvent を agent_responses に INSERT する。

    agent_role / model_used は AgentDoneEvent に含まれないため、
    呼び出し元（stream_and_persist）から明示的に渡す。
    token 数・latency は Phase 3 では未計測（nullable につき省略）。
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agent_responses
                (session_id, round, agent_id, agent_role, model_used, content)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (session_id, round, agent_id) DO NOTHING
            """,
            session_id,
            event.round,
            event.agent_id,
            agent_role,
            model_used,
            event.content,
        )


async def _persist_round_summary(
    pool: asyncpg.Pool,
    session_id: UUID,
    event: RoundSummaryEvent,
) -> None:
    next_status = STATUS_ROUND2 if event.round == 1 else STATUS_SYNTHESIS
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE deliberation_sessions SET status=$1 WHERE id=$2",
            next_status,
            session_id,
        )


async def _persist_synthesis_done(
    pool: asyncpg.Pool,
    session_id: UUID,
    event: SynthesisDoneEvent,
) -> None:
    solution_dict = dataclasses.asdict(event.synthesis)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE deliberation_sessions
            SET third_solution=$1, status='pre_mortem'
            WHERE id=$2
            """,
            json.dumps(solution_dict),
            session_id,
        )


async def _persist_pre_mortem_done(
    pool: asyncpg.Pool,
    session_id: UUID,
    event: PreMortemDoneEvent,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE deliberation_sessions
            SET third_solution = jsonb_set(
                third_solution,
                '{failure_scenarios}',
                $1::jsonb
            )
            WHERE id=$2
            """,
            json.dumps(event.failure_scenarios),
            session_id,
        )


async def _persist_complete(
    pool: asyncpg.Pool,
    session_id: UUID,
    event: CompleteEvent,
) -> None:
    """
    CompleteEvent で熟議完了を記録する。
    Phase 3.5：CompleteEvent.total_cost_usd（engine/pricing.py 集計値）を DB に書き込む。
    speed モードは 0.0（コスト追跡対象外）。
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE deliberation_sessions
            SET status=$1,
                duration_seconds=$2,
                total_cost_usd=$3
            WHERE id=$4
            """,
            STATUS_COMPLETED,
            int(event.duration_seconds),
            event.total_cost_usd,
            session_id,
        )


async def _mark_failed(pool: asyncpg.Pool, session_id: UUID, error: str) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE deliberation_sessions SET status=$1, error_message=$2 WHERE id=$3",
                STATUS_FAILED,
                error,
                session_id,
            )
    except Exception:
        logger.exception("_mark_failed でエラーが発生しました session_id=%s", session_id)


# ===========================
# fan-out generator（SSE クライアントへ配信）
# ===========================

async def stream_and_persist(
    pool: asyncpg.Pool,
    session_id: UUID,
    user_id: str,
    title: str,
    question: str,
    mode: Mode,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """
    stream_debate() を消費しながら DB 書き込みを行い、
    (event_name, event_dict) のタプルを yield する。

    Yields:
        (event_name, event_data_dict)
    """
    debate_model = _MODE_DEBATE_MODELS.get(mode, DEBATE_MODEL)

    # Phase 4 RAG：company_context と直近セッションを文脈として注入（設計書 §6.3）
    # 失敗しても空文字で継続（build_context_prompt 内部でフォールバック済み）
    memory_context = await build_context_prompt(pool, question, user_id=user_id)
    if memory_context:
        logger.info("RAG 文脈注入 session_id=%s chars=%d", session_id, len(memory_context))

    try:
        async for event in stream_debate(question=question, mode=mode, memory_context=memory_context):
            # DB 書き込み
            try:
                if isinstance(event, SessionStartEvent):
                    await _persist_session_start(
                        pool, session_id, user_id, title, question, mode, event
                    )
                elif isinstance(event, AgentDoneEvent):
                    agent_spec = AGENTS.get(event.agent_id)
                    agent_role = agent_spec.name if agent_spec else event.agent_id
                    await _persist_agent_done(pool, session_id, event, agent_role, debate_model)
                elif isinstance(event, RoundSummaryEvent):
                    await _persist_round_summary(pool, session_id, event)
                elif isinstance(event, SynthesisDoneEvent):
                    await _persist_synthesis_done(pool, session_id, event)
                elif isinstance(event, PreMortemDoneEvent):
                    await _persist_pre_mortem_done(pool, session_id, event)
                elif isinstance(event, CompleteEvent):
                    await _persist_complete(pool, session_id, event)
                # Phase 3.5 delta イベント・error イベントは DB 書き込みなし（SSE 中継のみ）
                # AgentContentDeltaEvent / AgentThinkingDeltaEvent / SynthesisDeltaEvent / AgentErrorEvent
            except Exception:
                logger.exception("DB 書き込みエラー（継続） event=%s", type(event).__name__)

            # SSE 配信
            yield event_name(event), _event_to_dict(event)

    except Exception as exc:
        logger.exception("stream_and_persist でエラー session_id=%s", session_id)
        await _mark_failed(pool, session_id, str(exc))
        raise


# ===========================
# バックグラウンド実行 + recovery（Opus 設計確定）
# ===========================

async def run_in_background(
    pool: asyncpg.Pool,
    session_id: UUID,
    user_id: str,
    title: str,
    question: str,
    mode: Mode,
    user_email: str | None = None,
) -> None:
    """
    asyncio.create_task で呼ばれる fire-and-forget タスク。

    Phase 3.5：完了後に notify_completion（N-002 メール通知）を呼ぶ。
    user_email が None の場合は通知をスキップ。
    """
    try:
        async for _ in stream_and_persist(pool, session_id, user_id, title, question, mode):
            pass  # SSE 配信不要
        logger.info("バックグラウンド熟議完了 session_id=%s", session_id)
        # N-002：完了メール通知（SMTP 未設定時はスキップ）
        if user_email:
            await notify_completion(
                session_id=session_id,
                user_email=user_email,
                title=title,
            )
    except Exception:
        logger.exception("バックグラウンド実行エラー session_id=%s", session_id)


async def recover_orphan_sessions(pool: asyncpg.Pool) -> int:
    """
    起動時に status が中途半端なセッションを 'failed' にリセットする。
    Opus 設計確定：プロセス再起動時の整合性保証。

    Returns:
        リセットした件数
    """
    orphan_statuses = ("pending", "round1", "round2", "synthesis", "pre_mortem")
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE deliberation_sessions
            SET status='failed', error_message='プロセス再起動によりセッションが中断されました'
            WHERE status = ANY($1::text[])
            """,
            list(orphan_statuses),
        )
    count = int(result.split()[-1])
    if count > 0:
        logger.warning("起動時 recovery: %d 件のセッションを failed に更新しました", count)
    return count
