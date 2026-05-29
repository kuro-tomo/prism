"""
ARIF — API テストスイート（Phase 3）

httpx.AsyncClient + ASGITransport で実 HTTP 往復不要のテスト。
dependency_overrides で認証・DB を完全 mock 化。

設計書 §7・仕様書 A-001〜A-004・B-001〜B-003・S-004 の
振る舞いを機械的に検証する。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.deps import User, get_current_user, get_pool
from api.main import app


# ===========================
# フィクスチャ
# ===========================

@pytest.fixture
def mock_user() -> User:
    return User(id=str(uuid.uuid4()), email="president@techno-chubu.co.jp")


@pytest.fixture
def mock_pool():
    """asyncpg.Pool の最小 mock。"""
    pool = MagicMock()

    # pool.acquire() → async context manager
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)

    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)

    return pool


@pytest.fixture(autouse=True)
def setup_app_state(mock_pool: Any) -> Any:
    """
    lifespan を経由しないテスト環境向け。
    全テストで app.state.pool を設定して get_pool が動作するようにする。
    """
    app.state.pool = mock_pool
    yield
    try:
        del app.state._state["pool"]
    except (KeyError, AttributeError):
        pass


@pytest.fixture
async def client(mock_user: User, mock_pool: Any) -> AsyncClient:
    """dependency_overrides で認証・DB を mock した AsyncClient。"""
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_pool] = lambda: mock_pool
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def unauth_client() -> AsyncClient:
    """
    認証を mock しない（401 テスト用）。
    get_pool は autouse の setup_app_state で app.state.pool が設定済みゆえ動作する。
    """
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ===========================
# ヘルスチェック
# ===========================

class TestHealth:
    async def test_health_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_health_returns_ok_status(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.json()["status"] == "ok"

    async def test_health_returns_version(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert "version" in resp.json()


# ===========================
# 熟議 POST（A-001）
# ===========================

class TestCreateDeliberation:
    async def test_create_returns_202(self, client: AsyncClient) -> None:
        resp = await client.post("/deliberations", json={
            "question": "当社の5年後をどう描くか？",
            "title": "テストセッション",
            "mode": "speed",
        })
        assert resp.status_code == 202

    async def test_create_returns_session_id(self, client: AsyncClient) -> None:
        resp = await client.post("/deliberations", json={
            "question": "当社の5年後をどう描くか？",
            "title": "テストセッション",
            "mode": "standard",
        })
        data = resp.json()
        assert "session_id" in data
        # UUID 形式確認
        uuid.UUID(data["session_id"])

    async def test_create_returns_stream_url(self, client: AsyncClient) -> None:
        resp = await client.post("/deliberations", json={
            "question": "テスト課題",
            "title": "テスト",
            "mode": "speed",
        })
        data = resp.json()
        assert "stream_url" in data
        assert "/deliberations/" in data["stream_url"]
        assert "/stream" in data["stream_url"]

    async def test_create_mode_reflected_in_response(self, client: AsyncClient) -> None:
        for mode in ("speed", "standard", "deep"):
            resp = await client.post("/deliberations", json={
                "question": "テスト課題",
                "title": "タイトル",
                "mode": mode,
            })
            assert resp.json()["mode"] == mode

    async def test_create_estimated_seconds_by_mode(self, client: AsyncClient) -> None:
        expected = {"speed": 30, "standard": 120, "deep": 300}
        for mode, seconds in expected.items():
            resp = await client.post("/deliberations", json={
                "question": "テスト", "title": "タイトル", "mode": mode,
            })
            assert resp.json()["estimated_seconds"] == seconds

    async def test_create_empty_question_raises_422(self, client: AsyncClient) -> None:
        resp = await client.post("/deliberations", json={
            "question": "",
            "title": "テスト",
        })
        assert resp.status_code == 422

    async def test_create_invalid_mode_raises_422(self, client: AsyncClient) -> None:
        resp = await client.post("/deliberations", json={
            "question": "テスト課題",
            "title": "テスト",
            "mode": "ultra",
        })
        assert resp.status_code == 422

    async def test_create_unauthenticated_returns_401(self, unauth_client: AsyncClient) -> None:
        resp = await unauth_client.post("/deliberations", json={
            "question": "テスト", "title": "タイトル",
        })
        assert resp.status_code == 401


# ===========================
# 熟議一覧 GET（A-002）
# ===========================

class TestListDeliberations:
    async def test_list_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/deliberations")
        assert resp.status_code == 200

    async def test_list_returns_array(self, client: AsyncClient) -> None:
        resp = await client.get("/deliberations")
        assert isinstance(resp.json(), list)

    async def test_list_unauthenticated_returns_401(self, unauth_client: AsyncClient) -> None:
        resp = await unauth_client.get("/deliberations")
        assert resp.status_code == 401


# ===========================
# 熟議詳細 GET（A-003）
# ===========================

class TestGetDeliberation:
    async def test_get_nonexistent_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get(f"/deliberations/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_get_unauthenticated_returns_401(self, unauth_client: AsyncClient) -> None:
        resp = await unauth_client.get(f"/deliberations/{uuid.uuid4()}")
        assert resp.status_code == 401


# ===========================
# フィードバック POST（B-001〜B-003）
# ===========================

class TestFeedback:
    async def test_feedback_returns_201_when_session_exists(
        self, client: AsyncClient, mock_pool: Any
    ) -> None:
        session_id = uuid.uuid4()
        # セッションが存在すると見なす mock
        mock_pool.acquire().__aenter__.return_value.fetchval = AsyncMock(
            return_value=session_id
        )
        resp = await client.post(
            f"/deliberations/{session_id}/feedback",
            json={"overall_rating": 5, "usefulness": 4, "novelty": 3},
        )
        assert resp.status_code == 201

    async def test_feedback_404_when_session_not_found(
        self, client: AsyncClient, mock_pool: Any
    ) -> None:
        mock_pool.acquire().__aenter__.return_value.fetchval = AsyncMock(return_value=None)
        resp = await client.post(
            f"/deliberations/{uuid.uuid4()}/feedback",
            json={"overall_rating": 3, "usefulness": 3, "novelty": 3},
        )
        assert resp.status_code == 404

    async def test_feedback_rating_out_of_range_raises_422(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post(
            f"/deliberations/{uuid.uuid4()}/feedback",
            json={"overall_rating": 6, "usefulness": 3, "novelty": 3},
        )
        assert resp.status_code == 422

    async def test_feedback_unauthenticated_returns_401(
        self, unauth_client: AsyncClient
    ) -> None:
        resp = await unauth_client.post(
            f"/deliberations/{uuid.uuid4()}/feedback",
            json={"overall_rating": 3, "usefulness": 3, "novelty": 3},
        )
        assert resp.status_code == 401


# ===========================
# コンテキスト（M-003）
# ===========================

class TestContext:
    async def test_list_context_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/context")
        assert resp.status_code == 200

    async def test_list_context_returns_array(self, client: AsyncClient) -> None:
        resp = await client.get("/context")
        assert isinstance(resp.json(), list)

    async def test_create_context_returns_201(self, client: AsyncClient) -> None:
        resp = await client.post("/context", json={
            "category": "strategy",
            "title": "中期事業計画",
            "content": "テクノ中部は製造業向けITソリューションを主力事業とする。",
        })
        assert resp.status_code == 201

    async def test_create_context_invalid_category_raises_422(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post("/context", json={
            "category": "unknown",
            "title": "テスト",
            "content": "内容",
        })
        assert resp.status_code == 422

    async def test_context_unauthenticated_returns_401(
        self, unauth_client: AsyncClient
    ) -> None:
        resp = await unauth_client.get("/context")
        assert resp.status_code == 401


# ===========================
# スキーマ（Pydantic バリデーション）
# ===========================

class TestSchemas:
    async def test_deliberation_request_max_question_length(
        self, client: AsyncClient
    ) -> None:
        """question が 4000 文字超えで 422（仕様書 F-001）。"""
        resp = await client.post("/deliberations", json={
            "question": "あ" * 4001,
            "title": "タイトル",
        })
        assert resp.status_code == 422

    async def test_deliberation_request_max_title_length(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post("/deliberations", json={
            "question": "テスト課題",
            "title": "あ" * 201,
        })
        assert resp.status_code == 422


# ===========================
# SSE ストリーム GET（A-004）
# ===========================

class TestStreamDeliberation:
    async def test_stream_unauthenticated_returns_401(
        self, unauth_client: AsyncClient
    ) -> None:
        """未認証は 401（仕様書 S-004）。"""
        resp = await unauth_client.get(
            f"/deliberations/{uuid.uuid4()}/stream",
            params={"question": "テスト課題", "title": "タイトル"},
        )
        assert resp.status_code == 401

    async def test_stream_invalid_mode_returns_200_with_error_event(
        self, client: AsyncClient
    ) -> None:
        """不正モードでも SSE は HTTP 200 を返し、error イベントをストリームする（設計書 §7.3）。"""
        resp = await client.get(
            f"/deliberations/{uuid.uuid4()}/stream",
            params={"question": "テスト", "title": "タイトル", "mode": "ultra"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        assert "error" in resp.text

    async def test_stream_valid_mode_returns_200(
        self, client: AsyncClient
    ) -> None:
        """正常モード + mock generator で HTTP 200 が返る（仕様書 A-004）。"""
        async def _mock_stream(*args: Any, **kwargs: Any):
            yield ("session_start", {"mode": "speed", "event_type": "session_start"})

        with patch("api.routes.deliberations.stream_and_persist", new=_mock_stream):
            resp = await client.get(
                f"/deliberations/{uuid.uuid4()}/stream",
                params={"question": "テスト", "title": "タイトル", "mode": "speed"},
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    async def test_stream_missing_question_returns_422(
        self, client: AsyncClient
    ) -> None:
        """question クエリパラメータ必須（FastAPI バリデーション）。"""
        resp = await client.get(
            f"/deliberations/{uuid.uuid4()}/stream",
            params={"title": "タイトル"},
        )
        assert resp.status_code == 422


# ===========================
# Phase 5T: 会社プロフィール API テスト
# ===========================

@pytest.mark.asyncio
class TestProfileAPI:
    """GET /profile・POST /profile の振る舞いを検証する（Phase 5T）。"""

    @pytest.fixture
    def client(self, mock_user: User, mock_pool: Any) -> AsyncClient:
        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[get_pool] = lambda: mock_pool
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def test_get_profile_json_returns_empty_when_not_registered(
        self, client: AsyncClient, mock_pool: Any
    ) -> None:
        """未登録ユーザーは空プロフィールが返る（GET /profile/json・HTTP 200）。"""
        conn = mock_pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value=None)
        resp = await client.get("/profile/json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["industry"] == ""
        assert data["scale"] == ""

    async def test_upsert_profile_returns_200_with_html(
        self, client: AsyncClient, mock_pool: Any
    ) -> None:
        """POST /profile（form-urlencoded）でプロフィールを登録できる（HTTP 200・HTML応答）。"""
        conn = mock_pool.acquire.return_value.__aenter__.return_value
        conn.execute = AsyncMock(return_value=None)
        form_data = {
            "industry": "建設機械部品メーカー",
            "scale": "売上100億円・従業員300名",
            "main_products": "油圧シリンダー",
            "main_customers": "国内建機メーカー3社",
            "strengths": "国内シェア15%",
            "avoid_directions": "自動車部品参入は断念済み",
            "free_context": "",
        }
        resp = await client.post("/profile", data=form_data)
        assert resp.status_code == 200
        assert "profile-saved-msg" in resp.text
        assert "保存しました" in resp.text

    async def test_upsert_profile_empty_form_returns_200(
        self, client: AsyncClient, mock_pool: Any
    ) -> None:
        """フィールド未入力（全空）でも 200 が返る（全フィールドにデフォルト値あり）。"""
        conn = mock_pool.acquire.return_value.__aenter__.return_value
        conn.execute = AsyncMock(return_value=None)
        resp = await client.post("/profile", data={})
        assert resp.status_code == 200
        assert "保存しました" in resp.text


# ===========================
# Phase 5T: Webサイト自動取得 API テスト（F-020）
# ===========================

@pytest.mark.asyncio
class TestProfileFetchAPI:
    """POST /profile/fetch の振る舞いを検証する（SSRF防御・エラー系）。"""

    @pytest.fixture
    def client(self, mock_user: User, mock_pool: Any) -> AsyncClient:
        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[get_pool] = lambda: mock_pool
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def test_ssrf_loopback_rejected(self, client: AsyncClient) -> None:
        """ループバックアドレスへの fetch は 200 で error フィールドが返る（SSRF防御）。"""
        resp = await client.post("/profile/fetch", json={"url": "http://127.0.0.1:8001/"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] != ""

    async def test_ssrf_metadata_rejected(self, client: AsyncClient) -> None:
        """クラウドメタデータエンドポイントへの fetch は拒否される。"""
        resp = await client.post("/profile/fetch", json={"url": "http://169.254.169.254/"})
        assert resp.status_code == 200
        assert resp.json()["error"] != ""

    async def test_ssrf_private_network_rejected(self, client: AsyncClient) -> None:
        """プライベートネットワークへの fetch は拒否される。"""
        resp = await client.post("/profile/fetch", json={"url": "http://192.168.1.1/"})
        assert resp.status_code == 200
        assert resp.json()["error"] != ""

    async def test_invalid_scheme_rejected(self, client: AsyncClient) -> None:
        """http/https 以外のスキームは拒否される。"""
        resp = await client.post("/profile/fetch", json={"url": "ftp://example.com/"})
        assert resp.status_code == 200
        assert resp.json()["error"] != ""

    async def test_empty_url_returns_422(self, client: AsyncClient) -> None:
        """空URLは Pydantic バリデーションで 422 を返す。"""
        resp = await client.post("/profile/fetch", json={"url": ""})
        assert resp.status_code == 422

    async def test_fetch_success_returns_extracted_fields(self, client: AsyncClient) -> None:
        """正常時は抽出フィールドが返り error は空（fetch_company_info をモック）。"""
        async def _mock_fetch(url: str, **kwargs: Any) -> dict[str, str]:
            return {
                "industry": "建設機械部品メーカー",
                "main_products": "油圧シリンダー",
                "main_customers": "国内建機メーカー",
                "strengths": "国内シェア15%",
            }
        with patch("api.routes.profile.fetch_company_info", new=_mock_fetch):
            resp = await client.post("/profile/fetch", json={"url": "https://example.co.jp/"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] == ""
        assert data["industry"] == "建設機械部品メーカー"
        assert data["main_products"] == "油圧シリンダー"
