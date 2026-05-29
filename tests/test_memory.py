"""
tests/test_memory.py — engine/memory.py のテストスイート (Phase 2)

仕様書 M-001〜M-005 / 設計書 §6 の検証。
asyncpg・OpenAI への外部接続はすべてモック化し、
ユニットテストとして完結させる。

テスト数: 30件
カバレッジ対象:
  - create_session / update_session_status / save_debate_result
  - save_agent_response
  - embed_text / search_similar_context / get_recent_sessions
  - build_context_prompt（正常系・接続失敗時のフォールバック）
  - list_sessions / get_session
  - 定数値の確認
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from engine.memory import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    RAG_RECENT_N,
    RAG_TOP_K,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_ROUND1,
    build_context_prompt,
    create_session,
    embed_text,
    get_company_profile,
    get_recent_sessions,
    get_session,
    list_sessions,
    save_agent_response,
    save_debate_result,
    search_similar_context,
    update_session_status,
)
from engine.debate import DebateResult, ThirdSolution
from engine import ARIF_DISCLAIMER


# ===========================
# ヘルパー：asyncpg pool モック
# ===========================

def make_pool(fetchrow_return=None, fetch_return=None, execute_return=None):
    """asyncpg.Pool の acquire() コンテキストマネージャを模倣する。"""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.execute = AsyncMock(return_value=execute_return)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


def make_third_solution() -> ThirdSolution:
    """テスト用 ThirdSolution を生成する。"""
    return ThirdSolution(
        conclusion="テスト結論",
        rationale=[{"agent": "strategist", "point": "戦略的に有効"}],
        actions_short_term=["3ヶ月以内に実施すべき施策"],
        actions_mid_term=["1〜3年のマイルストーン"],
        assumptions=["前提条件A"],
        minority_view=None,
        consensus_risk=False,
        failure_scenarios=["失敗シナリオ1"],
        guilford_scores=None,
    )


def make_debate_result() -> DebateResult:
    """テスト用 DebateResult を生成する。

    DebateResult の実際のフィールド構成（engine/debate.py）:
        question, memory_context, mode, round1, round2,
        synthesis, diversity_score_r1, diversity_score_r2,
        consensus_risk, total_cost_usd, duration_seconds
    """
    return DebateResult(
        question="テスト経営課題",
        memory_context="",
        mode="standard",
        synthesis=make_third_solution(),
        total_cost_usd=0.10,
        duration_seconds=60.0,
    )


# ===========================
# §1 定数値の確認
# ===========================

class TestConstants:
    def test_embedding_model_pinned(self) -> None:
        """仕様書 §7：embedding モデルはピン留め済みであること。"""
        assert EMBEDDING_MODEL == "voyage-3"

    def test_embedding_dimensions(self) -> None:
        """設計書 §6.1：embedding 次元は 1024（Voyage AI voyage-3）。"""
        assert EMBEDDING_DIMENSIONS == 1024

    def test_rag_top_k_default(self) -> None:
        """設計書 §6.3：RAG Top-K のデフォルト値は 3。"""
        assert RAG_TOP_K == 3

    def test_rag_recent_n_default(self) -> None:
        """設計書 §6.3：RAG 直近 N のデフォルト値は 2。"""
        assert RAG_RECENT_N == 2

    def test_status_constants_defined(self) -> None:
        """設計書 §6.1：全 status 定数が定義済みであること。"""
        from engine.memory import (
            STATUS_PENDING, STATUS_ROUND1, STATUS_ROUND2,
            STATUS_SYNTHESIS, STATUS_PRE_MORTEM, STATUS_COMPLETED, STATUS_FAILED,
        )
        statuses = {
            STATUS_PENDING, STATUS_ROUND1, STATUS_ROUND2,
            STATUS_SYNTHESIS, STATUS_PRE_MORTEM, STATUS_COMPLETED, STATUS_FAILED,
        }
        assert len(statuses) == 7, "status 定数が重複または不足"


# ===========================
# §2 create_session（仕様書 M-001）
# ===========================

class TestCreateSession:
    async def test_returns_uuid(self) -> None:
        """create_session は UUID を返す。"""
        fixed_id = uuid4()
        pool, conn = make_pool(fetchrow_return={"id": str(fixed_id)})
        result = await create_session(pool, question="経営課題", mode="standard")
        assert result == fixed_id

    async def test_title_auto_generated_from_question(self) -> None:
        """title 省略時は question の先頭 50 文字から自動生成される。"""
        fixed_id = uuid4()
        pool, conn = make_pool(fetchrow_return={"id": str(fixed_id)})
        question = "あ" * 60
        await create_session(pool, question=question, mode="standard")
        call_args = conn.fetchrow.call_args[0]
        # $1 が title
        assert call_args[1] == "あ" * 50 + "…"

    async def test_explicit_title_used_as_is(self) -> None:
        """title を明示した場合はそのまま使用される。"""
        fixed_id = uuid4()
        pool, conn = make_pool(fetchrow_return={"id": str(fixed_id)})
        await create_session(pool, question="課題", mode="speed", title="明示タイトル")
        call_args = conn.fetchrow.call_args[0]
        assert call_args[1] == "明示タイトル"

    async def test_status_is_pending(self) -> None:
        """INSERT 時の status は STATUS_PENDING であること。"""
        fixed_id = uuid4()
        pool, conn = make_pool(fetchrow_return={"id": str(fixed_id)})
        await create_session(pool, question="課題", mode="standard")
        call_args = conn.fetchrow.call_args[0]
        # $4 が status
        assert call_args[4] == STATUS_PENDING

    async def test_short_question_no_ellipsis(self) -> None:
        """50 文字以下の質問では末尾に '…' を付けない。"""
        fixed_id = uuid4()
        pool, conn = make_pool(fetchrow_return={"id": str(fixed_id)})
        short_q = "短い課題"
        await create_session(pool, question=short_q, mode="deep")
        call_args = conn.fetchrow.call_args[0]
        assert call_args[1] == short_q


# ===========================
# §3 update_session_status
# ===========================

class TestUpdateSessionStatus:
    async def test_status_updated(self) -> None:
        """status が指定値に更新される。"""
        pool, conn = make_pool()
        sid = uuid4()
        await update_session_status(pool, sid, STATUS_ROUND1)
        conn.execute.assert_awaited_once()
        sql, *args = conn.execute.call_args[0]
        assert STATUS_ROUND1 in args

    async def test_error_message_passed(self) -> None:
        """error_message が渡された場合は SQL に含まれる。"""
        pool, conn = make_pool()
        sid = uuid4()
        await update_session_status(pool, sid, STATUS_FAILED, error_message="timeout")
        _, *args = conn.execute.call_args[0]
        assert "timeout" in args


# ===========================
# §4 save_debate_result（仕様書 M-001）
# ===========================

class TestSaveDebateResult:
    async def test_third_solution_serialized_as_json(self) -> None:
        """ThirdSolution は JSONB として JSON シリアライズされる（設計書 §6.1）。"""
        pool, conn = make_pool()
        result = make_debate_result()
        await save_debate_result(pool, uuid4(), result)
        _, *args = conn.execute.call_args[0]
        json_str = args[1]
        assert json_str is not None
        parsed = json.loads(json_str)
        assert parsed["conclusion"] == "テスト結論"

    async def test_disclaimer_preserved_in_json(self) -> None:
        """ARIF_DISCLAIMER が JSONB に保持されること（仕様書 F-015）。"""
        pool, conn = make_pool()
        result = make_debate_result()
        await save_debate_result(pool, uuid4(), result)
        _, *args = conn.execute.call_args[0]
        parsed = json.loads(args[1])
        assert parsed["disclaimer"] == ARIF_DISCLAIMER

    async def test_status_becomes_completed(self) -> None:
        """保存後のステータスは STATUS_COMPLETED であること。"""
        pool, conn = make_pool()
        result = make_debate_result()
        await save_debate_result(pool, uuid4(), result)
        _, *args = conn.execute.call_args[0]
        assert args[0] == STATUS_COMPLETED

    async def test_no_third_solution_stores_null(self) -> None:
        """synthesis が None の DebateResult は JSONB に NULL を保存する。"""
        pool, conn = make_pool()
        result = DebateResult(
            question="q",
            memory_context="",
            mode="speed",
            synthesis=None,
            total_cost_usd=0.0,
            duration_seconds=0.0,
        )
        await save_debate_result(pool, uuid4(), result)
        _, *args = conn.execute.call_args[0]
        assert args[1] is None


# ===========================
# §5 save_agent_response
# ===========================

class TestSaveAgentResponse:
    async def test_insert_called_with_correct_args(self) -> None:
        """INSERT に必要な引数が渡される。"""
        pool, conn = make_pool()
        sid = uuid4()
        await save_agent_response(
            pool, sid,
            round_num=1, agent_id="strategist", agent_role="経営戦略家",
            model_used="claude-opus-4-20250514", temperature=0.7,
            content="戦略的提言",
        )
        conn.execute.assert_awaited_once()
        _, *args = conn.execute.call_args[0]
        assert args[0] == sid
        assert args[1] == 1
        assert args[2] == "strategist"
        assert args[6] == "戦略的提言"


# ===========================
# §6 embed_text（仕様書 M-004）
# ===========================

class TestEmbedText:
    async def test_returns_list_of_floats(self) -> None:
        """embed_text は float のリストを返す。"""
        mock_embedding = [0.1] * EMBEDDING_DIMENSIONS
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": mock_embedding}]}
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch.dict("os.environ", {"VOYAGE_API_KEY": "test-key"}):
                result = await embed_text("テスト")

        assert isinstance(result, list)
        assert len(result) == EMBEDDING_DIMENSIONS

    async def test_correct_model_used(self) -> None:
        """voyage-3 が使用される（仕様書 §7 ピン留め）。"""
        mock_embedding = [0.0] * EMBEDDING_DIMENSIONS
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": mock_embedding}]}
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            with patch.dict("os.environ", {"VOYAGE_API_KEY": "test-key"}):
                await embed_text("テスト")

        call_kwargs = mock_http.post.call_args[1]
        assert call_kwargs["json"]["model"] == EMBEDDING_MODEL


# ===========================
# §7 search_similar_context（仕様書 M-004）
# ===========================

class TestSearchSimilarContext:
    async def test_returns_list_of_dicts(self) -> None:
        """検索結果は dict のリストで返る。"""
        mock_rows = [
            {"title": "財務状況", "content": "内容", "category": "financial", "similarity": 0.9},
        ]
        pool, conn = make_pool(fetch_return=mock_rows)
        result = await search_similar_context(pool, [0.1] * EMBEDDING_DIMENSIONS)
        assert isinstance(result, list)
        assert result[0]["title"] == "財務状況"

    async def test_top_k_passed_to_query(self) -> None:
        """top_k が SQL の LIMIT に渡される。"""
        pool, conn = make_pool(fetch_return=[])
        await search_similar_context(pool, [0.0] * EMBEDDING_DIMENSIONS, top_k=5)
        _, *args = conn.fetch.call_args[0]
        assert 5 in args

    async def test_user_id_passed_to_query(self) -> None:
        """user_id が SQL 引数に渡される（Phase 4 RLS 補完）。"""
        uid = str(uuid4())
        pool, conn = make_pool(fetch_return=[])
        await search_similar_context(pool, [0.0] * EMBEDDING_DIMENSIONS, user_id=uid)
        _, *args = conn.fetch.call_args[0]
        assert uid in args

    async def test_no_user_id_omits_filter(self) -> None:
        """user_id 未指定時は引数に含まれない。"""
        pool, conn = make_pool(fetch_return=[])
        await search_similar_context(pool, [0.0] * EMBEDDING_DIMENSIONS)
        _, *args = conn.fetch.call_args[0]
        # args = [top_k] のみ（embedding_str は positional 1番目）
        assert all(not isinstance(a, str) or a.startswith("[") for a in args)

    async def test_excludes_null_embedding(self) -> None:
        """SQL に embedding IS NOT NULL 句が含まれる（仕様書 F-019）。

        embedding 生成は非同期のため、生成完了前の NULL レコードが
        RAG 検索に混入してはならない。SQL レベルで除外することを保証する。
        """
        pool, conn = make_pool(fetch_return=[])
        await search_similar_context(pool, [0.0] * EMBEDDING_DIMENSIONS)
        sql = conn.fetch.call_args[0][0]
        assert "embedding IS NOT NULL" in sql

    async def test_null_filter_precedes_validity_filter(self) -> None:
        """NULL 除外が WHERE 句の先頭にあり、有効期限フィルタと AND 結合される。"""
        pool, conn = make_pool(fetch_return=[])
        await search_similar_context(pool, [0.0] * EMBEDDING_DIMENSIONS)
        sql = conn.fetch.call_args[0][0]
        # WHERE embedding IS NOT NULL AND (valid_until ...) の順序
        null_pos = sql.index("embedding IS NOT NULL")
        valid_pos = sql.index("valid_until")
        assert null_pos < valid_pos


# ===========================
# §8 get_recent_sessions（仕様書 M-002）
# ===========================

class TestGetRecentSessions:
    async def test_returns_completed_sessions(self) -> None:
        """直近の completed セッションが返る。"""
        now = datetime(2026, 5, 27, tzinfo=timezone.utc)
        mock_rows = [
            {
                "id": uuid4(), "title": "前回の熟議", "question": "前回の課題",
                "mode": "standard", "conclusion": "前回の結論", "created_at": now,
            }
        ]
        pool, conn = make_pool(fetch_return=mock_rows)
        result = await get_recent_sessions(pool)
        assert len(result) == 1
        assert result[0]["conclusion"] == "前回の結論"

    async def test_n_passed_to_limit(self) -> None:
        """n が SQL の LIMIT に渡される。"""
        pool, conn = make_pool(fetch_return=[])
        await get_recent_sessions(pool, n=5)
        _, *args = conn.fetch.call_args[0]
        assert 5 in args

    async def test_user_id_passed_to_query(self) -> None:
        """user_id が SQL 引数に渡される（Phase 4 RLS 補完）。"""
        uid = str(uuid4())
        pool, conn = make_pool(fetch_return=[])
        await get_recent_sessions(pool, user_id=uid)
        _, *args = conn.fetch.call_args[0]
        assert uid in args

    async def test_no_user_id_single_arg(self) -> None:
        """user_id 未指定時は n のみが SQL 引数。"""
        pool, conn = make_pool(fetch_return=[])
        await get_recent_sessions(pool, n=2)
        _, *args = conn.fetch.call_args[0]
        assert args == [2]


# ===========================
# §9 build_context_prompt（設計書 §6.3）
# ===========================

class TestBuildContextPrompt:
    async def test_returns_string(self) -> None:
        """build_context_prompt は str を返す。"""
        pool, conn = make_pool(fetch_return=[])
        with patch("engine.memory.embed_text", new=AsyncMock(return_value=[0.0] * EMBEDDING_DIMENSIONS)):
            result = await build_context_prompt(pool, "テスト課題")
        assert isinstance(result, str)

    async def test_empty_when_no_data(self) -> None:
        """コンテキストもセッションも無い場合は空文字を返す。"""
        pool, conn = make_pool(fetch_return=[])
        with patch("engine.memory.embed_text", new=AsyncMock(return_value=[0.0] * EMBEDDING_DIMENSIONS)):
            result = await build_context_prompt(pool, "課題")
        assert result == ""

    async def test_fallback_on_exception(self) -> None:
        """接続失敗時は空文字を返し例外を送出しない（熟議継続優先）。"""
        pool, _ = make_pool()
        with patch("engine.memory.embed_text", new=AsyncMock(side_effect=ConnectionError("接続失敗"))):
            result = await build_context_prompt(pool, "課題")
        assert result == ""

    async def test_context_included_in_output(self) -> None:
        """company_context のデータが出力テキストに含まれる。"""
        mock_rows = [
            {"title": "市場環境", "content": "競合増加中", "category": "market", "similarity": 0.8}
        ]
        pool = MagicMock()
        # search_similar_context と get_recent_sessions を個別にモック
        call_count = [0]

        async def mock_fetch(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_rows   # search_similar_context 用
            return []              # get_recent_sessions 用

        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=mock_fetch)
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("engine.memory.embed_text", new=AsyncMock(return_value=[0.0] * EMBEDDING_DIMENSIONS)):
            result = await build_context_prompt(pool, "課題")
        assert "市場環境" in result
        assert "競合増加中" in result


# ===========================
# §10 list_sessions / get_session（仕様書 A-002, A-003）
# ===========================

class TestListAndGetSession:
    async def test_list_sessions_returns_list(self) -> None:
        """list_sessions は dict のリストを返す。"""
        now = datetime(2026, 5, 27, tzinfo=timezone.utc)
        mock_rows = [
            {
                "id": uuid4(), "title": "熟議1", "question": "q1",
                "mode": "standard", "status": STATUS_COMPLETED,
                "total_cost_usd": 0.15, "duration_seconds": 90, "created_at": now,
            }
        ]
        pool, conn = make_pool(fetch_return=mock_rows)
        result = await list_sessions(pool)
        assert isinstance(result, list)
        assert result[0]["title"] == "熟議1"

    async def test_list_sessions_limit_offset_passed(self) -> None:
        """limit と offset が SQL に渡される。"""
        pool, conn = make_pool(fetch_return=[])
        await list_sessions(pool, limit=10, offset=5)
        _, *args = conn.fetch.call_args[0]
        assert 10 in args
        assert 5 in args

    async def test_get_session_returns_dict(self) -> None:
        """get_session は dict を返す。"""
        sid = uuid4()
        pool, conn = make_pool(fetchrow_return={
            "id": sid, "title": "熟議", "question": "課題",
            "mode": "standard", "status": STATUS_COMPLETED,
            "third_solution": '{"conclusion": "結論"}',
            "total_input_tokens": 1000, "total_output_tokens": 500,
            "total_cost_usd": 0.10, "duration_seconds": 60,
            "error_message": None, "created_at": None, "updated_at": None,
            "agent_responses": "[]",
        })
        result = await get_session(pool, sid)
        assert result is not None
        assert result["title"] == "熟議"

    async def test_get_session_returns_none_when_not_found(self) -> None:
        """存在しないセッション ID の場合は None を返す。"""
        pool, conn = make_pool(fetchrow_return=None)
        result = await get_session(pool, uuid4())
        assert result is None


# ===========================
# Phase 5T: get_company_profile テスト
# ===========================

@pytest.mark.asyncio
class TestGetCompanyProfile:
    """get_company_profile() の振る舞いを検証する（Phase 5T・設計書 §6.4）。"""

    async def test_returns_empty_when_no_user_id(self) -> None:
        """user_id 未指定の場合は空文字を返す。"""
        pool, _ = make_pool(fetchrow_return=None)
        result = await get_company_profile(pool, user_id=None)
        assert result == ""

    async def test_returns_empty_when_profile_not_registered(self) -> None:
        """プロフィール未登録の場合は空文字を返す。"""
        pool, _ = make_pool(fetchrow_return=None)
        result = await get_company_profile(pool, user_id="user-123")
        assert result == ""

    async def test_returns_profile_text_with_all_fields(self) -> None:
        """全フィールド登録済みの場合、ヘッダー＋各フィールドが含まれる。"""
        profile_row = {
            "industry": "建設機械部品メーカー",
            "scale": "売上100億円・従業員300名",
            "main_products": "油圧シリンダー",
            "main_customers": "国内建機メーカー3社",
            "strengths": "国内シェア15%",
            "avoid_directions": "自動車部品参入は断念済み",
            "free_context": "後継者問題あり",
        }
        pool, _ = make_pool(fetchrow_return=profile_row)
        result = await get_company_profile(pool, user_id="user-123")
        assert "会社プロフィール" in result
        assert "建設機械部品メーカー" in result
        assert "油圧シリンダー" in result
        assert "自動車部品参入は断念済み" in result

    async def test_skips_empty_fields(self) -> None:
        """空フィールドは出力に含まれない。"""
        profile_row = {
            "industry": "建設機械部品メーカー",
            "scale": "",
            "main_products": "",
            "main_customers": "",
            "strengths": "",
            "avoid_directions": "",
            "free_context": "",
        }
        pool, _ = make_pool(fetchrow_return=profile_row)
        result = await get_company_profile(pool, user_id="user-123")
        assert "建設機械部品メーカー" in result
        assert "規模" not in result   # 空フィールドは出力されない

    async def test_returns_empty_on_db_error(self) -> None:
        """DB 接続エラー時は空文字を返し、熟議を止めない。"""
        from unittest.mock import AsyncMock, MagicMock
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(side_effect=Exception("DB error"))
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await get_company_profile(pool, user_id="user-123")
        assert result == ""

    async def test_build_context_prompt_includes_profile_at_top(self) -> None:
        """build_context_prompt がプロフィールを文脈の先頭に配置する（設計書 §6.4）。"""
        profile_row = {
            "industry": "建設機械部品メーカー",
            "scale": "売上100億円",
            "main_products": "油圧シリンダー",
            "main_customers": "建機メーカー",
            "strengths": "国内シェア15%",
            "avoid_directions": "自動車参入は断念",
            "free_context": "",
        }
        # fetchrow は company_profile を返し、
        # fetch は company_context（empty）と recent（empty）を返すよう mock
        pool = MagicMock()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=profile_row)
        conn.fetch = AsyncMock(return_value=[])
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "engine.memory.embed_text",
                AsyncMock(return_value=[0.0] * 1024),
            )
            result = await build_context_prompt(pool, "5年後の主力事業は？", user_id="user-123")

        # プロフィールが先頭にある（インデックス 0 付近）
        assert result.index("会社プロフィール") < 50
        assert "建設機械部品メーカー" in result
