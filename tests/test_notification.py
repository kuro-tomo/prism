"""
tests/test_notification.py — 通知サービス テスト（Phase 3.5・N-002）

テスト構成:
  TestSendCompletionEmail   — send_completion_email() の動作検証
  TestNotifyCompletion      — notify_completion() のラッパー動作検証

検証方針:
  - SMTP 設定の有無による分岐（スキップ vs 送信試行）
  - aiosmtplib.send のモック化で実際の SMTP 接続を回避
  - 送信失敗時の返り値・ログ出力（セッション停止させない設計）
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest


# ────────────────────────────────────────────────────────────────────
# TestSendCompletionEmail
# ────────────────────────────────────────────────────────────────────

class TestSendCompletionEmail:
    """send_completion_email() の単体テスト"""

    @pytest.mark.asyncio
    async def test_returns_false_when_smtp_not_configured(self) -> None:
        """SMTP 設定が未完了の場合 False を返してスキップすること（N-002）"""
        from api.services.notification import send_completion_email
        with patch.dict("os.environ", {}, clear=True):
            # 環境変数なし → False 返却
            result = await send_completion_email(
                to_email="test@example.com",
                session_id=uuid.uuid4(),
                title="テスト熟議",
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_smtp_configured_and_send_succeeds(self) -> None:
        """SMTP 設定が完了しており aiosmtplib.send が成功した場合 True を返すこと"""
        from api.services.notification import send_completion_email
        smtp_env = {
            "ARIF_SMTP_HOST": "smtp.example.com",
            "ARIF_SMTP_PORT": "587",
            "ARIF_SMTP_USER": "user@example.com",
            "ARIF_SMTP_PASS": "secret",
            "ARIF_SMTP_FROM": "noreply@example.com",
            "ARIF_BASE_URL": "https://arif.example.com",
        }
        with patch.dict("os.environ", smtp_env):
            with patch("aiosmtplib.send", new=AsyncMock(return_value=None)) as mock_send:
                result = await send_completion_email(
                    to_email="president@techno-chubu.co.jp",
                    session_id=uuid.uuid4(),
                    title="テスト熟議",
                )
        assert result is True
        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_email_recipient_is_correct(self) -> None:
        """送信先メールアドレスが正しく EmailMessage に設定されること"""
        from api.services.notification import send_completion_email
        smtp_env = {
            "ARIF_SMTP_HOST": "smtp.example.com",
            "ARIF_SMTP_USER": "user",
            "ARIF_SMTP_PASS": "pass",
            "ARIF_SMTP_FROM": "noreply@example.com",
        }
        captured_msg: list = []

        async def _capture(msg, **kwargs):  # type: ignore[no-untyped-def]
            captured_msg.append(msg)

        with patch.dict("os.environ", smtp_env):
            with patch("aiosmtplib.send", new=_capture):
                await send_completion_email(
                    to_email="target@example.com",
                    session_id=uuid.uuid4(),
                    title="テスト熟議",
                )
        assert len(captured_msg) == 1
        assert captured_msg[0]["To"] == "target@example.com"

    @pytest.mark.asyncio
    async def test_email_subject_contains_title(self) -> None:
        """件名に熟議タイトルが含まれること（N-003 遷移リンク用）"""
        from api.services.notification import send_completion_email
        smtp_env = {
            "ARIF_SMTP_HOST": "smtp.example.com",
            "ARIF_SMTP_USER": "user",
            "ARIF_SMTP_PASS": "pass",
            "ARIF_SMTP_FROM": "noreply@example.com",
        }
        captured_msg: list = []

        async def _capture(msg, **kwargs):  # type: ignore[no-untyped-def]
            captured_msg.append(msg)

        title = "5年後の主力事業戦略"
        with patch.dict("os.environ", smtp_env):
            with patch("aiosmtplib.send", new=_capture):
                await send_completion_email(
                    to_email="test@example.com",
                    session_id=uuid.uuid4(),
                    title=title,
                )
        assert title in captured_msg[0]["Subject"]

    @pytest.mark.asyncio
    async def test_email_body_contains_result_url(self) -> None:
        """メール本文に結果URLが含まれること（N-003 1クリック遷移）"""
        from api.services.notification import send_completion_email
        smtp_env = {
            "ARIF_SMTP_HOST": "smtp.example.com",
            "ARIF_SMTP_USER": "user",
            "ARIF_SMTP_PASS": "pass",
            "ARIF_SMTP_FROM": "noreply@example.com",
            "ARIF_BASE_URL": "https://arif.techno-chubu.co.jp",
        }
        session_id = uuid.uuid4()
        captured_msg: list = []

        async def _capture(msg, **kwargs):  # type: ignore[no-untyped-def]
            captured_msg.append(msg)

        with patch.dict("os.environ", smtp_env):
            with patch("aiosmtplib.send", new=_capture):
                await send_completion_email(
                    to_email="test@example.com",
                    session_id=session_id,
                    title="テスト",
                )
        # get_content() は Base64/QP デコード済みの文字列を返す
        body = captured_msg[0].get_content()
        assert str(session_id) in body
        assert "arif.techno-chubu.co.jp" in body

    @pytest.mark.asyncio
    async def test_returns_false_on_smtp_error(self) -> None:
        """SMTP 送信中に例外が発生した場合 False を返すこと（セッション停止しない）"""
        from api.services.notification import send_completion_email
        smtp_env = {
            "ARIF_SMTP_HOST": "smtp.example.com",
            "ARIF_SMTP_USER": "user",
            "ARIF_SMTP_PASS": "pass",
            "ARIF_SMTP_FROM": "noreply@example.com",
        }
        with patch.dict("os.environ", smtp_env):
            with patch("aiosmtplib.send", new=AsyncMock(side_effect=ConnectionRefusedError("mock error"))):
                result = await send_completion_email(
                    to_email="test@example.com",
                    session_id=uuid.uuid4(),
                    title="テスト",
                )
        assert result is False

    @pytest.mark.asyncio
    async def test_partial_smtp_config_skips(self) -> None:
        """SMTP 設定が部分的に未設定の場合もスキップして False を返すこと"""
        from api.services.notification import send_completion_email
        # SMTP_PASS のみ未設定
        smtp_env = {
            "ARIF_SMTP_HOST": "smtp.example.com",
            "ARIF_SMTP_USER": "user",
            "ARIF_SMTP_FROM": "noreply@example.com",
        }
        with patch.dict("os.environ", smtp_env, clear=True):
            result = await send_completion_email(
                to_email="test@example.com",
                session_id=uuid.uuid4(),
                title="テスト",
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_port_465_uses_implicit_tls(self) -> None:
        """port 465（SMTPS）では use_tls=True / start_tls=False で呼び出されること（Opus指摘③）"""
        from api.services.notification import send_completion_email
        smtp_env = {
            "ARIF_SMTP_HOST": "smtp.example.com",
            "ARIF_SMTP_PORT": "465",   # SMTPS
            "ARIF_SMTP_USER": "user",
            "ARIF_SMTP_PASS": "pass",
            "ARIF_SMTP_FROM": "noreply@example.com",
        }
        captured_kwargs: list[dict] = []

        async def _capture(msg, **kwargs):  # type: ignore[no-untyped-def]
            captured_kwargs.append(kwargs)

        with patch.dict("os.environ", smtp_env):
            with patch("aiosmtplib.send", new=_capture):
                await send_completion_email(
                    to_email="test@example.com",
                    session_id=uuid.uuid4(),
                    title="テスト",
                )
        assert len(captured_kwargs) == 1
        assert captured_kwargs[0]["use_tls"] is True
        assert captured_kwargs[0]["start_tls"] is False

    @pytest.mark.asyncio
    async def test_port_587_uses_starttls(self) -> None:
        """port 587（submission）では use_tls=False / start_tls=True で呼び出されること（Opus指摘③）"""
        from api.services.notification import send_completion_email
        smtp_env = {
            "ARIF_SMTP_HOST": "smtp.example.com",
            "ARIF_SMTP_PORT": "587",
            "ARIF_SMTP_USER": "user",
            "ARIF_SMTP_PASS": "pass",
            "ARIF_SMTP_FROM": "noreply@example.com",
        }
        captured_kwargs: list[dict] = []

        async def _capture(msg, **kwargs):  # type: ignore[no-untyped-def]
            captured_kwargs.append(kwargs)

        with patch.dict("os.environ", smtp_env):
            with patch("aiosmtplib.send", new=_capture):
                await send_completion_email(
                    to_email="test@example.com",
                    session_id=uuid.uuid4(),
                    title="テスト",
                )
        assert len(captured_kwargs) == 1
        assert captured_kwargs[0]["use_tls"] is False
        assert captured_kwargs[0]["start_tls"] is True


# ────────────────────────────────────────────────────────────────────
# TestNotifyCompletion
# ────────────────────────────────────────────────────────────────────

class TestNotifyCompletion:
    """notify_completion() のラッパー動作テスト"""

    @pytest.mark.asyncio
    async def test_calls_send_completion_email_when_email_provided(self) -> None:
        """user_email が設定されている場合 send_completion_email を呼ぶこと"""
        from api.services.notification import notify_completion
        session_id = uuid.uuid4()

        with patch(
            "api.services.notification.send_completion_email",
            new=AsyncMock(return_value=True),
        ) as mock_send:
            await notify_completion(
                session_id=session_id,
                user_email="president@techno-chubu.co.jp",
                title="テスト熟議",
            )
        mock_send.assert_called_once_with(
            to_email="president@techno-chubu.co.jp",
            session_id=session_id,
            title="テスト熟議",
        )

    @pytest.mark.asyncio
    async def test_skips_when_email_empty(self) -> None:
        """user_email が空の場合 send_completion_email を呼ばないこと"""
        from api.services.notification import notify_completion

        with patch(
            "api.services.notification.send_completion_email",
            new=AsyncMock(return_value=True),
        ) as mock_send:
            await notify_completion(
                session_id=uuid.uuid4(),
                user_email="",  # 空
                title="テスト熟議",
            )
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_notify_email_false(self) -> None:
        """notify_email=False の場合 send_completion_email を呼ばないこと"""
        from api.services.notification import notify_completion

        with patch(
            "api.services.notification.send_completion_email",
            new=AsyncMock(return_value=True),
        ) as mock_send:
            await notify_completion(
                session_id=uuid.uuid4(),
                user_email="test@example.com",
                title="テスト熟議",
                notify_email=False,
            )
        mock_send.assert_not_called()
