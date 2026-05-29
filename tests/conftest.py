"""
tests/conftest.py — pytest フィクスチャ定義

全テストファイルから利用可能なフィクスチャを一元管理する。
モッククライアントの実装は tests/mock_anthropic.py に分離。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.mock_anthropic import make_client, make_failing_create, make_failing_stream


@pytest.fixture
def mock_client() -> MagicMock:
    """
    標準動作の AsyncAnthropic クライアントモック。
    messages.create（バッチ）と messages.stream（ストリーミング）の両方をモック化。
    全ラウンド・Pre-mortem が正常完走するシナリオ。
    """
    return make_client()


@pytest.fixture
def mock_client_all_failing() -> MagicMock:
    """
    全エージェントが失敗するクライアントモック（create / stream 両方失敗）。
    RuntimeError（MIN_AGENTS_FOR_SYNTHESIS 未達）のテスト用。
    speed モード（batch）・standard/deep モード（stream）の両方に対応。
    """
    return make_client(
        create_mock=make_failing_create(),
        stream_mock=make_failing_stream(),
    )
