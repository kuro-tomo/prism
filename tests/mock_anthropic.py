"""
tests/mock_anthropic.py — AsyncAnthropic クライアントモック

run_debate() の E2E テストで実 API 呼び出しを行わないためのモック群。
呼び出し内容（プロンプトのキーワード）に基づいて適切なレスポンスを返す。

方針（Opus 審査 2026-05-27 指摘事項）:
  - プロンプト本文の完全一致テストは禁忌（将来の改善反復を阻害するため）
  - 識別ロジックは「ラウンド識別に使うキーワード」に限定し、文言に依存しない
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from anthropic.types import TextBlock


class _AsyncChunkIter:
    """
    async iterable of str chunks（stream.text_stream のモック）。

    Anthropic SDK の text_stream と同じインターフェース。
    各インスタンスは一度だけ消費できる（async generator と同等）。
    """

    def __init__(self, text: str, n_chunks: int = 3) -> None:
        chunk_size = max(1, len(text) // max(1, n_chunks))
        self._chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

    def __aiter__(self) -> "_AsyncChunkIter":
        return self

    async def __anext__(self) -> str:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

# ── モックレスポンス ──────────────────────────────────────────────────

# Round 1 / Round 2 エージェント発言
MOCK_AGENT_RESPONSE = (
    "この経営課題について専門家として独自の視点から分析する。"
    "現状のリスクを踏まえた段階的アプローチが有効であり、"
    "市場動向を考慮すると新規参入の余地がある。"
    "具体的には短期・中期の施策を組み合わせることで持続的成長が見込める。"
)

# Round 1 / Round 2 要約（Haiku）
MOCK_SUMMARY = (
    "各エージェントの要約: strategist は長期成長戦略を、cfo はROI最大化を、"
    "engineer は実現可能性を、market は顧客ニーズへの適合を、"
    "risk は潜在リスクの管理をそれぞれ主張した。"
    "全体として方向性には一致が見られつつも、優先順位に差異がある。"
)

# Round 3 統合（Opus）— 有効な JSON
MOCK_SYNTHESIS_JSON = """{
  "conclusion": "A案とB案の統合により第三の解を導くべきである。なぜなら strategist の成長戦略と market の顧客要求が干渉して既存の二項対立を超えた第三の道が生まれたから。",
  "rationale": [
    {"agent": "strategist", "point": "長期的な市場ポジションに新市場開拓が不可欠"},
    {"agent": "cfo", "point": "ROIは18ヶ月で回収見込みであり財務的に許容範囲"},
    {"agent": "market", "point": "顧客ニーズが既存製品ラインと乖離しており転換機会"}
  ],
  "actions_short_term": [
    "市場調査チームを即時発足させる",
    "パイロット予算500万円を確保する",
    "プロジェクトオーナーを任命する"
  ],
  "actions_mid_term": [
    "2027年Q1 新製品ラインを3本稼働",
    "2028年 海外展開の初期検証"
  ],
  "assumptions": [
    "競合が6ヶ月以内に同方向へ展開しないこと",
    "調達コストが15%以内の変動に収まること"
  ],
  "minority_view": null,
  "consensus_risk": false,
  "guilford_scores": {
    "fluency": 4,
    "flexibility": 4,
    "originality": 4,
    "elaboration": 4
  }
}"""

# Pre-mortem（Opus）— 有効な JSON 配列
MOCK_PRE_MORTEM_JSON = (
    '["市場が想定より早く飽和し需要が消失した",'
    ' "資金調達が遅延し実行タイミングを逸した",'
    ' "主要人材が競合へ流出した",'
    ' "競合が先行して市場を制定した",'
    ' "顧客ニーズが急変し製品仮定が外れた"]'
)


# ── モックオブジェクト生成 ────────────────────────────────────────────

def make_message(text: str) -> MagicMock:
    """
    anthropic.types.Message のモックを生成する。
    content[0] は実際の TextBlock を使用する（isinstance チェックを通過させるため）。
    """
    msg = MagicMock()
    msg.content = [TextBlock(text=text, type="text")]
    msg.usage = MagicMock(input_tokens=100, output_tokens=50)
    return msg


def make_smart_create() -> AsyncMock:
    """
    呼び出し内容に基づいて適切なモックレスポンスを返す AsyncMock を作成する。

    識別ロジック（プロンプトキーワード — 優先度順）:
      "大失敗していました" → Pre-mortem（JSON 配列）★ 最優先（結論文に「第三の解」を含む場合があるため）
      "品質基準"          → Round 3 統合（JSON オブジェクト）
      "エージェント発言"   → 要約（Summary R1 / R2）
      それ以外             → エージェント発言（Round 1 / Round 2）
    """
    async def _side_effect(**kwargs: object) -> MagicMock:
        messages = kwargs.get("messages", [])
        user_content = ""
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                user_content = str(msg.get("content", ""))

        # Pre-mortem を最優先でチェック（結論文に「第三の解」等が含まれる場合があるため）
        if "大失敗していました" in user_content:
            return make_message(MOCK_PRE_MORTEM_JSON)
        # 統合プロンプト固有キーワード（品質基準は Round 3 のみ）
        if "品質基準" in user_content:
            return make_message(MOCK_SYNTHESIS_JSON)
        if "エージェント発言" in user_content:
            return make_message(MOCK_SUMMARY)
        # Round 1 / Round 2 エージェント発言（system プロンプト経由）
        return make_message(MOCK_AGENT_RESPONSE)

    return AsyncMock(side_effect=_side_effect)


def make_failing_create() -> AsyncMock:
    """
    常に ValueError を発生させる AsyncMock。
    全エージェント失敗シナリオのテスト用（RuntimeError 検証）。
    """
    async def _side_effect(**kwargs: object) -> MagicMock:
        raise ValueError("mock: 意図的なエージェント全失敗")

    return AsyncMock(side_effect=_side_effect)


def make_failing_stream() -> MagicMock:
    """
    messages.stream() が常に ValueError を発生させる MagicMock。
    ストリーミング全エージェント失敗シナリオのテスト用。
    """
    def _side_effect(**kwargs: object) -> MagicMock:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=ValueError("mock: 意図的なストリーム全失敗"))
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    mock = MagicMock(side_effect=_side_effect)
    return mock


def _make_stream_ctx(text: str) -> MagicMock:
    """
    messages.stream() の戻り値となる async context manager モックを生成する。

    async with client.messages.stream(**kwargs) as stream:
        async for chunk in stream.text_stream: ...
        final = await stream.get_final_message()
    """
    final_msg = make_message(text)

    stream = MagicMock()
    stream.text_stream = _AsyncChunkIter(text)
    stream.get_final_message = AsyncMock(return_value=final_msg)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=stream)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def make_smart_stream() -> MagicMock:
    """
    呼び出し内容に基づいて適切なストリーミングモックを返す。

    識別ロジックは make_smart_create() と同一（プロンプトキーワード優先順）。
    stream_debate() の standard / deep モードで messages.stream() の代わりに使用。
    """
    def _side_effect(**kwargs: object) -> MagicMock:
        messages = kwargs.get("messages", [])
        user_content = ""
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                user_content = str(msg.get("content", ""))

        if "大失敗していました" in user_content:
            return _make_stream_ctx(MOCK_PRE_MORTEM_JSON)
        if "品質基準" in user_content:
            return _make_stream_ctx(MOCK_SYNTHESIS_JSON)
        if "エージェント発言" in user_content:
            return _make_stream_ctx(MOCK_SUMMARY)
        return _make_stream_ctx(MOCK_AGENT_RESPONSE)

    mock = MagicMock(side_effect=_side_effect)
    return mock


def make_client(
    create_mock: AsyncMock | None = None,
    stream_mock: MagicMock | None = None,
) -> MagicMock:
    """
    AsyncAnthropic クライアントのモックを生成する。

    Args:
        create_mock:  messages.create に使用する AsyncMock（省略時: make_smart_create()）
        stream_mock:  messages.stream に使用する MagicMock（省略時: make_smart_stream()）

    Note:
        stream_mock は messages.stream(**kwargs) の return_value として機能する
        ため、make_smart_stream() の side_effect が各呼び出しで fresh な
        async context manager を返す設計になっている。
    """
    client = MagicMock()
    client.messages.create = create_mock or make_smart_create()
    client.messages.stream = stream_mock or make_smart_stream()
    return client
