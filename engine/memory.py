"""
engine/memory.py — Supabase 永続化・RAG 文脈生成

仕様書 M-001〜M-005 の実装。
設計書 §6 データベース設計・§6.3 RAG 戦略に準拠。

依存:
    asyncpg>=0.29.0   — 非同期 PostgreSQL 接続（pgvector クエリ）
    httpx>=0.27.0     — Voyage AI embedding API クライアント

環境変数:
    SUPABASE_DB_URL  — asyncpg 接続文字列
                       例: postgresql://arif_service:<pass>@db.<ref>.supabase.co:5432/postgres
    VOYAGE_API_KEY   — Voyage AI embedding API キー（仕様書 S-001 準拠）
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Optional
from uuid import UUID

import asyncpg
import httpx

from engine.debate import DebateResult

# ===========================
# 定数（設計書 §6.3）
# ===========================
EMBEDDING_MODEL: str = "voyage-3"  # 仕様書 §7 ピン留め（Voyage AI）
EMBEDDING_DIMENSIONS: int = 1024
RAG_TOP_K: int = 3          # ベクトル類似 Top-K（設計書 §6.3）
RAG_RECENT_N: int = 2       # 直近 N 件（設計書 §6.3）

# ===========================
# セッション status 定数（設計書 §6.1）
# ===========================
STATUS_PENDING: str = "pending"
STATUS_ROUND1: str = "round1"
STATUS_ROUND2: str = "round2"
STATUS_SYNTHESIS: str = "synthesis"
STATUS_PRE_MORTEM: str = "pre_mortem"
STATUS_COMPLETED: str = "completed"
STATUS_FAILED: str = "failed"


# ===========================
# DB 接続ヘルパー（仕様書 M-001）
# ===========================

async def get_db_pool(dsn: Optional[str] = None) -> asyncpg.Pool:
    """asyncpg コネクションプールを生成して返す。

    Args:
        dsn: PostgreSQL 接続文字列。省略時は環境変数 SUPABASE_DB_URL を使用。

    Returns:
        asyncpg.Pool

    Raises:
        KeyError: SUPABASE_DB_URL が未設定の場合。
    """
    url = dsn or os.environ["SUPABASE_DB_URL"]
    # search_path=arif を設定し、テーブル名プレフィックス（arif.xxx）不要にする
    # pr-scribe DB 内の arif PostgreSQL スキーマを使用（設計書 §6 参照）
    # ssl="require": Render等の本番環境では Supabase への SSL 接続が必須
    #   ローカル開発でも Supabase Direct Connection は SSL を受け入れるため問題なし
    pool: asyncpg.Pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=5,
        server_settings={"search_path": "arif"},
        ssl="require",
    )
    return pool


# ===========================
# セッション CRUD（仕様書 M-001）
# ===========================

async def create_session(
    pool: asyncpg.Pool,
    *,
    question: str,
    mode: str,
    title: str = "",
) -> UUID:
    """熟議セッションを pending 状態で作成し、UUID を返す。

    Args:
        pool:     asyncpg コネクションプール
        question: 経営課題テキスト（仕様書 F-001）
        mode:     熟議モード（"speed" / "standard" / "deep"・仕様書 F-009）
        title:    タイトル。省略時は question の先頭 50 文字

    Returns:
        作成されたセッションの UUID
    """
    if not title:
        title = question[:50] + ("…" if len(question) > 50 else "")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO deliberation_sessions (title, question, mode, status)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            title,
            question,
            mode,
            STATUS_PENDING,
        )
    return UUID(str(row["id"]))


async def create_research_session(
    pool: asyncpg.Pool,
    *,
    question: str,
    mode: str,
    title: str = "",
) -> UUID:
    """研究用熟議セッションを pending 状態で作成し、UUID を返す（仕様書 F-022）。

    is_research = true を設定することで、本番 RAG（search_similar_sessions /
    get_recent_sessions）の検索対象から除外される。
    実験データが通常ユーザーの熟議文脈に混入するのを防ぐ（RAG 汚染防止）。

    Args:
        pool:     asyncpg コネクションプール
        question: 標準化シナリオの経営課題テキスト
        mode:     熟議モード（"speed" / "standard" / "deep"）
        title:    タイトル。省略時は question の先頭 50 文字

    Returns:
        作成されたセッションの UUID
    """
    if not title:
        title = question[:50] + ("…" if len(question) > 50 else "")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO deliberation_sessions (title, question, mode, status, is_research)
            VALUES ($1, $2, $3, $4, true)
            RETURNING id
            """,
            title,
            question,
            mode,
            STATUS_PENDING,
        )
    return UUID(str(row["id"]))


async def update_session_status(
    pool: asyncpg.Pool,
    session_id: UUID,
    status: str,
    *,
    error_message: Optional[str] = None,
) -> None:
    """セッション状態を更新する。

    Args:
        pool:          asyncpg コネクションプール
        session_id:    対象セッション UUID
        status:        新しいステータス文字列（STATUS_* 定数を使用）
        error_message: failed 遷移時のエラーメッセージ（任意）
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE deliberation_sessions
            SET status        = $1,
                error_message = COALESCE($2, error_message)
            WHERE id = $3
            """,
            status,
            error_message,
            session_id,
        )


async def save_debate_result(
    pool: asyncpg.Pool,
    session_id: UUID,
    result: DebateResult,
    *,
    voyage_api_key: str | None = None,
) -> None:
    """DebateResult を deliberation_sessions に保存して completed へ遷移する。

    ThirdSolution（result.synthesis）は JSONB として JSON シリアライズして保存する（設計書 §6.1）。
    question_embedding は Voyage AI voyage-3 で生成し同時保存する（仕様書 F-021・設計書 §6.3）。
    embedding 生成失敗はセッション保存を阻害しない（try/except で続行）。

    Args:
        pool:           asyncpg コネクションプール
        session_id:     対象セッション UUID
        result:         run_debate() の戻り値
        voyage_api_key: Voyage AI API キー（省略時は環境変数 VOYAGE_API_KEY）
    """
    third_solution_json: Optional[str] = None
    if result.synthesis is not None:
        third_solution_json = json.dumps(
            asdict(result.synthesis), ensure_ascii=False
        )

    # question_embedding 生成（失敗してもセッション保存は継続）
    question_embedding_str: Optional[str] = None
    try:
        embedding = await embed_text(result.question, api_key=voyage_api_key)
        question_embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
    except Exception:
        pass  # 次回熟議のRAG精度は低下するが、このセッション保存は成功させる

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE deliberation_sessions
            SET
                status              = $1,
                third_solution      = $2::jsonb,
                total_cost_usd      = $3,
                duration_seconds    = $4,
                question_embedding  = $5::vector,
                diversity_score_r1  = $6,
                diversity_score_r2  = $7
            WHERE id = $8
            """,
            STATUS_COMPLETED,
            third_solution_json,
            float(result.total_cost_usd),
            int(result.duration_seconds),
            question_embedding_str,
            result.diversity_score_r1,
            result.diversity_score_r2,
            session_id,
        )


async def save_agent_response(
    pool: asyncpg.Pool,
    session_id: UUID,
    *,
    round_num: int,
    agent_id: str,
    agent_role: str,
    model_used: str,
    temperature: float,
    content: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
    error: Optional[str] = None,
) -> None:
    """エージェント発言を agent_responses に INSERT する。

    AgentDoneEvent 受信時に呼び出す（段階書き込み・設計書 §6.1）。
    同一 (session_id, round, agent_id) が既に存在する場合は content を上書きする。

    Args:
        pool:          asyncpg コネクションプール
        session_id:    対象セッション UUID
        round_num:     ラウンド番号（1 / 2 / 3）
        agent_id:      エージェント ID（"strategist" 等）
        agent_role:    エージェント役割名
        model_used:    使用モデル文字列（ピン留めバージョン）
        temperature:   温度パラメータ
        content:       発言本文
        input_tokens:  入力トークン数
        output_tokens: 出力トークン数
        cost_usd:      コスト（USD）
        latency_ms:    レイテンシ（ミリ秒）
        error:         エラーメッセージ（失敗時）
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agent_responses
                (session_id, round, agent_id, agent_role, model_used, temperature,
                 content, input_tokens, output_tokens, cost_usd, latency_ms, error)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (session_id, round, agent_id) DO UPDATE
                SET content       = EXCLUDED.content,
                    input_tokens  = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    cost_usd      = EXCLUDED.cost_usd,
                    latency_ms    = EXCLUDED.latency_ms,
                    error         = EXCLUDED.error
            """,
            session_id,
            round_num,
            agent_id,
            agent_role,
            model_used,
            temperature,
            content,
            input_tokens,
            output_tokens,
            cost_usd,
            latency_ms,
            error,
        )


# ===========================
# RAG — Embedding（仕様書 M-004）
# ===========================

async def embed_text(
    text: str,
    *,
    api_key: str | None = None,
) -> list[float]:
    """Voyage AI voyage-3 でテキストをベクトル化する（仕様書 §7）。

    Args:
        text:    ベクトル化するテキスト
        api_key: Voyage AI API キー。省略時は環境変数 VOYAGE_API_KEY を使用。

    Returns:
        長さ 1024 の float リスト
    """
    _api_key = api_key or os.environ["VOYAGE_API_KEY"]
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {_api_key}"},
            json={"input": [text], "model": EMBEDDING_MODEL},
        )
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]


async def search_similar_context(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    *,
    top_k: int = RAG_TOP_K,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """pgvector コサイン類似度で company_context を検索する（仕様書 M-004・F-019）。

    embedding が NULL のレコードは除外する（仕様書 F-019）。
      理由：embedding 生成は非同期（context.py の create_task）のため、
      登録直後は NULL の窓が存在する。NULL を除外しないと
      `embedding <=> $1::vector` が NULL を返し（NULLS LAST）、
      有効レコードが top_k 未満のとき NULL 行が混入して
      similarity=NULL の的外れな文脈が注入される。
    有効期限（valid_until）が現在日付以前のレコードは除外する。
    user_id を指定すると当該ユーザーのコンテキストのみ対象とする（Phase 4 RLS 補完）。

    Args:
        pool:            asyncpg コネクションプール
        query_embedding: 質問文のベクトル（長さ 1024・voyage-3）
        top_k:           取得件数（既定: RAG_TOP_K=3）
        user_id:         ユーザー ID（指定時に行フィルタ）

    Returns:
        [{"title": str, "content": str, "category": str, "similarity": float}, ...]
    """
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    user_filter = " AND user_id = $3" if user_id else ""
    args: list[object] = [embedding_str, top_k]
    if user_id:
        args.append(user_id)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                title,
                content,
                category,
                1 - (embedding <=> $1::vector) AS similarity
            FROM company_context
            WHERE embedding IS NOT NULL
              AND (valid_until IS NULL OR valid_until >= CURRENT_DATE){user_filter}
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            *args,
        )
    return [dict(r) for r in rows]


async def search_similar_sessions(
    pool: asyncpg.Pool,
    query_embedding: list[float],
    *,
    top_k: int = RAG_TOP_K,
    user_id: str | None = None,
    exclude_ids: list[UUID] | None = None,
) -> list[dict[str, Any]]:
    """pgvector コサイン類似度で過去の熟議セッションを検索する（仕様書 F-021・設計書 §6.3）。

    question_embedding が NULL のレコードは除外する。
    status = 'completed' のセッションのみ対象とする（失敗・中断セッションを注入しない）。
    exclude_ids を指定すると当該セッションを除外する（直近セッションとの重複排除用）。

    Args:
        pool:            asyncpg コネクションプール
        query_embedding: 質問文のベクトル（長さ 1024・voyage-3）
        top_k:           取得件数（既定: RAG_TOP_K=3）
        user_id:         ユーザー ID（指定時に行フィルタ）
        exclude_ids:     除外するセッション UUID リスト

    Returns:
        [{"session_id": UUID, "question": str, "conclusion": str, "similarity": float}, ...]
    """
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    args: list[object] = [embedding_str, top_k]

    user_filter = ""
    if user_id:
        args.append(user_id)
        user_filter = f" AND user_id = ${len(args)}"

    exclude_filter = ""
    if exclude_ids:
        args.append([str(uid) for uid in exclude_ids])
        exclude_filter = f" AND id != ALL(${len(args)}::uuid[])"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                id AS session_id,
                question,
                third_solution ->> 'conclusion' AS conclusion,
                1 - (question_embedding <=> $1::vector) AS similarity
            FROM deliberation_sessions
            WHERE status = 'completed'
              AND question_embedding IS NOT NULL
              AND third_solution IS NOT NULL
              AND is_research = false{user_filter}{exclude_filter}
            ORDER BY question_embedding <=> $1::vector
            LIMIT $2
            """,
            *args,
        )
    return [dict(r) for r in rows]


async def get_recent_sessions(
    pool: asyncpg.Pool,
    *,
    n: int = RAG_RECENT_N,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """直近 N 件の completed セッションを取得する（仕様書 M-002）。

    注入するのは third_solution->>'conclusion' のみ（トークン予算節約）。
    user_id を指定すると当該ユーザーのセッションのみ対象とする（Phase 4 RLS 補完）。

    Args:
        pool:    asyncpg コネクションプール
        n:       取得件数（既定: RAG_RECENT_N=2）
        user_id: ユーザー ID（指定時に行フィルタ）

    Returns:
        [{"id": UUID, "title": str, "question": str, "conclusion": str|None, "created_at": datetime}, ...]
    """
    user_filter = " AND user_id = $2" if user_id else ""
    args: list[object] = [n]
    if user_id:
        args.append(user_id)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                id,
                title,
                question,
                mode,
                third_solution ->> 'conclusion' AS conclusion,
                created_at
            FROM deliberation_sessions
            WHERE status = 'completed'
              AND third_solution IS NOT NULL
              AND is_research = false{user_filter}
            ORDER BY created_at DESC
            LIMIT $1
            """,
            *args,
        )
    return [dict(r) for r in rows]


async def get_company_profile(
    pool: asyncpg.Pool,
    *,
    user_id: str | None = None,
) -> str:
    """会社プロフィールを取得し、固定注入用テキストとして返す（設計書 §6.4）。

    user_id が指定されていれば当該ユーザーのプロフィールのみ取得する。
    プロフィール未登録の場合は空文字を返す（熟議を止めない設計）。

    Returns:
        全熟議の memory_context 先頭に挿入する会社基本前提テキスト
    """
    if not user_id:
        return ""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT industry, scale, main_products, main_customers,
                       strengths, avoid_directions, free_context
                FROM company_profile
                WHERE user_id = $1
                """,
                user_id,
            )
        if not row:
            return ""

        parts: list[str] = ["【会社プロフィール（基本前提・必ず踏まえること）】"]
        field_labels = [
            ("industry",         "業種・事業内容"),
            ("scale",            "規模"),
            ("main_products",    "主力製品・サービス"),
            ("main_customers",   "主要顧客・販売先"),
            ("strengths",        "現在の状況・強み"),
            ("avoid_directions", "避けたい方向"),
            ("free_context",     "その他の文脈"),
        ]
        for field, label in field_labels:
            value = str(row[field] or "").strip()
            if value:
                parts.append(f"・{label}：{value}")

        return "\n".join(parts) if len(parts) > 1 else ""
    except Exception:
        return ""


async def build_context_prompt(
    pool: asyncpg.Pool,
    question: str,
    *,
    user_id: str | None = None,
    voyage_api_key: str | None = None,
) -> str:
    """RAG 文脈を構築してエージェントプロンプトへの注入用テキストを返す（設計書 §6.3）。

    フロー:
        ① get_company_profile()                            — 会社プロフィールを固定注入
        ② embed_text(question)                             — 質問を一度だけベクトル化
        ③ search_similar_context(pool, top_k=3, user_id)  — company_context から類似上位3件
        ④ get_recent_sessions(pool, n=2, user_id)          — 直近2件の結論を取得
        ⑤ search_similar_sessions(top_k=3, exclude_ids)   — 類似過去熟議 Top-3（直近2件を除外）
        ⑥ テキスト結合（~5,000 tokens 以内）

    接続失敗時は空文字を返し、熟議は文脈なしで継続する（熟議全体を止めない設計）。

    Args:
        pool:           asyncpg コネクションプール
        question:       熟議の経営課題テキスト
        user_id:        ユーザー ID（指定時に各サブ関数でフィルタ）
        voyage_api_key: Voyage AI API キー（テスト時に注入可）

    Returns:
        エージェントプロンプトに注入する文脈テキスト（空文字の場合あり）
    """
    # ① 会社プロフィール（固定注入・課題の類似度に依存しない）
    profile_text = await get_company_profile(pool, user_id=user_id)

    try:
        # ② 質問を一度だけベクトル化し ③⑤ 両方に使い回す
        embedding = await embed_text(question, api_key=voyage_api_key)
        # ③ company_context から類似コンテキスト
        similar = await search_similar_context(pool, embedding, user_id=user_id)
        # ④ 直近セッション
        recent = await get_recent_sessions(pool, user_id=user_id)
        # ⑤ 類似過去熟議（直近セッションとの重複を除外）
        recent_ids = [UUID(str(s["id"])) for s in recent if s.get("id")]
        similar_sessions = await search_similar_sessions(
            pool, embedding, user_id=user_id, exclude_ids=recent_ids or None,
        )
    except Exception:
        # 接続失敗時は文脈なしで継続（熟議全体を止めない）
        return profile_text  # プロフィールだけでも返す

    parts: list[str] = []

    # プロフィールを最先頭に固定配置（RAG より優先）
    if profile_text:
        parts.append(profile_text)

    if similar:
        parts.append("\n【会社コンテキスト（関連情報）】")
        for item in similar:
            category = item.get("category", "")
            title = item.get("title", "")
            content = str(item.get("content", ""))[:300]
            parts.append(f"[{category}] {title}: {content}")

    if recent:
        parts.append("\n【過去の熟議（直近の決定事項）】")
        for s in recent:
            created_at = s.get("created_at")
            dt_str = (
                created_at.strftime("%Y-%m-%d")
                if created_at is not None
                else ""
            )
            q = str(s.get("question", ""))[:60]
            conclusion = s.get("conclusion") or "（未確定）"
            parts.append(f"- {dt_str} 「{q}」→ {conclusion}")

    if similar_sessions:
        parts.append("\n【類似の過去熟議（関連課題の結論）】")
        for s in similar_sessions:
            q = str(s.get("question", ""))[:60]
            conclusion = s.get("conclusion") or "（未確定）"
            parts.append(f"- 「{q}」→ {conclusion}")

    return "\n".join(parts)


# ===========================
# セッション一覧・詳細取得（仕様書 A-002, A-003）
# ===========================

async def list_sessions(
    pool: asyncpg.Pool,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """過去の議論一覧を取得する（仕様書 A-002）。

    Args:
        pool:   asyncpg コネクションプール
        limit:  最大取得件数（既定: 20）
        offset: オフセット（ページング用）

    Returns:
        セッション概要のリスト（third_solution は含まない）
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id,
                title,
                question,
                mode,
                status,
                total_cost_usd,
                duration_seconds,
                created_at
            FROM deliberation_sessions
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
    return [dict(r) for r in rows]


async def get_session(
    pool: asyncpg.Pool,
    session_id: UUID,
) -> Optional[dict[str, Any]]:
    """特定セッションの詳細を取得する（仕様書 A-003）。

    エージェント発言ログも JSON 集約して返す。

    Args:
        pool:       asyncpg コネクションプール
        session_id: 対象セッション UUID

    Returns:
        セッション詳細 dict（存在しない場合は None）
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                ds.id,
                ds.title,
                ds.question,
                ds.mode,
                ds.status,
                ds.third_solution,
                ds.total_input_tokens,
                ds.total_output_tokens,
                ds.total_cost_usd,
                ds.duration_seconds,
                ds.error_message,
                ds.created_at,
                ds.updated_at,
                COALESCE(
                    json_agg(
                        json_build_object(
                            'round',      ar.round,
                            'agent_id',   ar.agent_id,
                            'agent_role', ar.agent_role,
                            'content',    ar.content,
                            'latency_ms', ar.latency_ms,
                            'error',      ar.error
                        ) ORDER BY ar.round, ar.agent_id
                    ) FILTER (WHERE ar.id IS NOT NULL),
                    '[]'::json
                ) AS agent_responses
            FROM deliberation_sessions ds
            LEFT JOIN agent_responses ar ON ar.session_id = ds.id
            WHERE ds.id = $1
            GROUP BY ds.id
            """,
            session_id,
        )
    return dict(row) if row else None
