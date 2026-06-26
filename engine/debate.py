"""
3ラウンド熟議エンジン（Phase 1A/1B 本実装 — 2026-05-27）

フロー:
  Round 1 — 各エージェントが独立回答（asyncio.gather で完全並列・相互参照なし）
  Round 2 — 全意見を共有した上で反論・深掘り（反論義務テンプレート §4.2 適用）
  Round 3 — 統合プロンプトが「第三の解」を確定的な提案として起草
  Pre-mortem — 常足・熟考モードのみ（失敗原因5つを生成し ThirdSolution に埋め込む）

公開 API:
  run_debate()    — バッチ版（Phase 1A）。全ラウンド完走後に DebateResult を返す
  stream_debate() — ストリーミング版（Phase 1B）。進行に合わせて DebateEvent を yield

設計原則（仕様書 §1.1）:
  「FIRAは議論し、ARIFは答える。」
  AIが最善の一手を提案し、最終判断は社長が下す。

出力フォーマット（仕様書 §4.4・設計書 §5.4）:
  ThirdSolution — 結論・論拠・アクションプラン・前提条件・少数意見・Pre-mortem
                  ・品質スコア（Guilford 4次元）・固定免責文言

エラー耐性（設計書 §8）:
  - 1体の失敗でセッション全体を停止させない（スキップして進行）
  - MIN_AGENTS_FOR_SYNTHESIS 未満で生存 → RuntimeError
  - RateLimitError / 529 Overloaded → 指数バックオフ（最大3回）
  - 1体あたりタイムアウト 120秒
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, AsyncIterator, Final, Literal, Union

import anthropic
from anthropic.types import TextBlock

from engine.agents import (
    AGENTS,
    DEBATE_MODEL,
    MAX_TOKENS_DEBATE,
    MAX_TOKENS_SUMMARY,
    SUMMARY_MODEL,
    AgentSpec,
    get_roster,
)
from engine.pricing import compute_cost, total_cost
from engine.utils import convergence_check, diversity_score

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────
# 定数
# ────────────────────────────────────────────────────────────────────

# 免責文言は固定文（仕様書 F-015・§4.4・改変禁止）
ARIF_DISCLAIMER: str = (
    "本提案は PRISM が熟議を経て導出した最善の一手です。"
    "最終判断は社長が下されるものです。"
)

# 熟議制御定数
MIN_AGENTS_FOR_SYNTHESIS: Final[int] = 3    # 統合に必要な最低生存エージェント数

# 匿名査読：Round 1 要約のエージェントID→匿名ラベルマッピング（合意バイアス防止・仕様書 §4.2）
_ANON_LABELS: Final[dict[str, str]] = {
    "strategist": "意見A",
    "cfo":        "意見B",
    "engineer":   "意見C",
    "market":     "意見D",
    "risk":       "意見E",
}
_SEMAPHORE_LIMIT: Final[int] = 4            # 同時API呼び出し上限（設計書 §3 concurrency_limit）
_TIMEOUT_SECONDS: Final[float] = 120.0     # 1体あたりタイムアウト（設計書 §8）
_RETRY_DELAYS: Final[tuple[float, ...]] = (1.0, 2.0, 4.0)  # 指数バックオフ待機秒数

Mode = Literal["speed", "standard", "deep"]

# モード別モデル選択（config.yaml §3 modes に対応）
_MODE_CONFIG: Final[dict[str, dict[str, object]]] = {
    "speed": {
        "debate_model":    SUMMARY_MODEL,  # Haiku で高速化（常足の約1/6コスト）
        "summary_model":   SUMMARY_MODEL,
        "synthesis_model": SUMMARY_MODEL,
        "pre_mortem":      False,           # 早足 = Pre-mortem 省略
    },
    "standard": {
        "debate_model":    DEBATE_MODEL,   # Opus
        "summary_model":   SUMMARY_MODEL,  # 要約は Haiku でコスト削減
        "synthesis_model": DEBATE_MODEL,
        "pre_mortem":      True,
    },
    "deep": {
        "debate_model":    DEBATE_MODEL,   # Opus
        "summary_model":   DEBATE_MODEL,   # 要約も Opus で精緻化
        "synthesis_model": DEBATE_MODEL,
        "pre_mortem":      True,
    },
}


# ────────────────────────────────────────────────────────────────────
# データクラス定義
# ────────────────────────────────────────────────────────────────────

@dataclass
class ThirdSolution:
    """
    第三の解：Round 3 の確定的出力フォーマット
    仕様書 §4.4・設計書 §5.4・CFIM白書 §3.3 準拠

    設計原則：AIが最善の一手を提案し、最終判断は社長が下す。
    全フィールドに default を持たせ、段階的に充填可能とする
    （Round 3 完了時に主要フィールド、Pre-mortem 完了時に failure_scenarios）。
    """

    # ■ 結論（必須・断定形1文）
    conclusion: str = ""
    # 例: "OEM依存を断ち切り自社ブランドへ集中すべきである。
    #      なぜなら strategist の成長戦略と market の顧客要求が干渉して
    #      「ブランド転換→OEM脱出」という第三の道が生まれたから。"

    # ■ 論拠（必須・3エージェント以上の論点を統合）
    rationale: list[dict[str, str]] = field(default_factory=list)
    # 各要素のキーは "agent"（エージェントID）と "point"（論点要約）に固定
    # 例: [
    #   {"agent": "strategist", "point": "5年後のポジショニングに自社ブランドが不可欠"},
    #   {"agent": "cfo",        "point": "OEM単価下落でROIが2年後に逆転"},
    #   {"agent": "market",     "point": "顧客調査でブランド認知欲求が確認された"},
    # ]

    # ■ アクションプラン（必須）
    actions_short_term: list[str] = field(default_factory=list)   # 3ヶ月以内（3〜5項目）
    actions_mid_term: list[str] = field(default_factory=list)     # 1〜3年のマイルストーン

    # ■ 前提条件（必須）
    assumptions: list[str] = field(default_factory=list)
    # これが崩れれば結論が変わる条件を明記
    # 例: ["競合がブランド転換を同時実施しないこと", "既存OEM顧客の離反率が20%未満"]

    # ■ 少数意見（拮抗時のみ）
    minority_view: str | None = None       # 強く反対したエージェントの論点
    consensus_risk: bool = False           # True = 全員一致検出（合意リスクあり・要注意）

    # ■ Pre-mortem（常足・熟考モードのみ。早足は空リスト）
    # pre_mortem_done イベント受信後に充填される設計（設計書 §7.3・§9.3 参照）
    failure_scenarios: list[str] = field(default_factory=list)

    # ■ 品質スコア（Guilford 4次元・任意）
    # SSE synthesis_done で配信し、Supabase 永続化対象
    guilford_scores: dict[str, int] | None = None
    # 例: {"fluency": 4, "flexibility": 5, "originality": 4, "elaboration": 5}

    # ■ 固定免責文言（全モード・全出力に必須。改変禁止）
    disclaimer: str = ARIF_DISCLAIMER

    def __post_init__(self) -> None:
        """免責文言の改変を防ぐ防壁（F-015）"""
        if self.disclaimer != ARIF_DISCLAIMER:
            raise ValueError(
                "ThirdSolution.disclaimer は固定文。改変禁止（仕様書 F-015）。"
            )


@dataclass
class DebateResult:
    """熟議セッション全体の結果コンテナ"""

    question: str
    memory_context: str
    mode: str = "standard"

    # 各ラウンドのエージェント発言（agent_id → response text）
    round1: dict[str, str] = field(default_factory=dict)
    round2: dict[str, str] = field(default_factory=dict)

    # 第三の解（Round 3 完了後に格納）
    synthesis: ThirdSolution | None = None

    # 品質指標（設計書 §9）
    diversity_score_r1: float = 0.0
    diversity_score_r2: float = 0.0
    consensus_risk: bool = False           # Round 2 収束チェック結果

    # パフォーマンス
    total_cost_usd: float = 0.0
    duration_seconds: float = 0.0


# ────────────────────────────────────────────────────────────────────
# SSE イベント型（Phase 1B — 設計書 §7.3 準拠）
# ────────────────────────────────────────────────────────────────────

@dataclass
class SessionStartEvent:
    """セッション開始（最初の 1 件）"""
    mode: str
    event_type: str = "session_start"


@dataclass
class RoundStartEvent:
    """ラウンド開始（Round 1 / Round 2 それぞれ 1 件）"""
    round: int
    agents: list[str] = field(default_factory=list)
    event_type: str = "round_start"


@dataclass
class AgentDoneEvent:
    """
    エージェント発言完了（完了した順に 1 件ずつ yield）。
    stream_debate() では asyncio.as_completed により
    完走順に配信するため、Round 内の順序は不定。
    """
    round: int
    agent_id: str
    content: str = ""
    event_type: str = "agent_done"


@dataclass
class RoundSummaryEvent:
    """
    ラウンド要約・多様性スコア（各ラウンド完了後 1 件）。
    diversity_score_phi は設計書 §9.1 の Φ 測定値。
    """
    round: int
    diversity_score_phi: float
    consensus_risk: bool = False
    event_type: str = "round_summary"


@dataclass
class SynthesisDoneEvent:
    """第三の解完成（Round 3 完了後 1 件）"""
    synthesis: ThirdSolution
    event_type: str = "synthesis_done"


@dataclass
class PreMortemDoneEvent:
    """Pre-mortem 完了（常足・熟考モードのみ 1 件）"""
    failure_scenarios: list[str] = field(default_factory=list)
    event_type: str = "pre_mortem_done"


@dataclass
class CompleteEvent:
    """熟議完了（最後の 1 件）"""
    duration_seconds: float
    total_cost_usd: float = 0.0   # Phase 3.5: コスト追跡（engine/pricing.py 集計）
    event_type: str = "complete"


# ────────────────────────────────────────────────────────────────────
# Phase 3.5 追加イベント（トークン単位ストリーミング・設計書 §7.3）
# standard / deep モードのみ emit。speed モードは節目イベントのみ。
# ────────────────────────────────────────────────────────────────────

@dataclass
class AgentContentDeltaEvent:
    """エージェント発言のトークン単位 delta（F-010）"""
    round: int
    agent_id: str
    text_chunk: str = ""
    event_type: str = "agent_content_delta"


@dataclass
class AgentThinkingDeltaEvent:
    """Extended Thinking delta（deep モード・F-012）。Phase 3.5 は定義のみ、emit は Phase 4。"""
    round: int
    agent_id: str
    thinking_chunk: str = ""
    event_type: str = "agent_thinking_delta"


@dataclass
class SynthesisDeltaEvent:
    """統合（第三の解）のトークン単位 delta（F-010）"""
    text_chunk: str = ""
    event_type: str = "synthesis_delta"


@dataclass
class AgentErrorEvent:
    """エージェント呼び出しエラー（streaming 中断時・継続可能）"""
    round: int
    agent_id: str
    error: str = ""
    event_type: str = "agent_error"


# DebateEvent — stream_debate() が yield する型の Union
DebateEvent = Union[
    SessionStartEvent,
    RoundStartEvent,
    AgentDoneEvent,
    AgentContentDeltaEvent,
    AgentThinkingDeltaEvent,
    SynthesisDeltaEvent,
    AgentErrorEvent,
    RoundSummaryEvent,
    SynthesisDoneEvent,
    PreMortemDoneEvent,
    CompleteEvent,
]


# ────────────────────────────────────────────────────────────────────
# 内部ヘルパー関数
# ────────────────────────────────────────────────────────────────────

async def _call_with_retry(
    client: anthropic.AsyncAnthropic,
    **kwargs: object,
) -> anthropic.types.Message:
    """
    指数バックオフで最大3回リトライする API 呼び出し（設計書 §8）。

    リトライ対象:
      - RateLimitError（HTTP 429）
      - APIStatusError HTTP 529（Anthropic 過負荷）
    上記以外の例外は即時再送出する。
    """
    last_exc: Exception | None = None
    for attempt, delay in enumerate((0.0, *_RETRY_DELAYS), start=1):
        if delay > 0.0:
            await asyncio.sleep(delay)
        try:
            return await client.messages.create(**kwargs)  # type: ignore[call-overload]
        except anthropic.RateLimitError as exc:
            logger.warning("Rate limit（試行 %d/%d）: %s", attempt, len(_RETRY_DELAYS) + 1, exc)
            last_exc = exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 529:
                logger.warning("API 過負荷（試行 %d/%d）: %s", attempt, len(_RETRY_DELAYS) + 1, exc)
                last_exc = exc
            else:
                raise  # 429・529 以外は即時送出
    assert last_exc is not None
    raise last_exc


async def _call_agent(
    agent: AgentSpec,
    user_message: str,
    client: anthropic.AsyncAnthropic,
    model: str,
    sem: asyncio.Semaphore,
) -> tuple[str, str | Exception]:
    """
    単体エージェントの API 呼び出し。
    セマフォで同時実行数を制限し、タイムアウトを適用する。

    Returns:
        (agent_id, response_text) 成功時
        (agent_id, Exception)     失敗時（gather 側でスキップ判定）
    """
    async with sem:
        try:
            response = await asyncio.wait_for(
                _call_with_retry(
                    client,
                    model=model,
                    max_tokens=MAX_TOKENS_DEBATE,
                    temperature=agent.temperature,
                    system=agent.system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                ),
                timeout=_TIMEOUT_SECONDS,
            )
            text: str = _extract_text(response)
            return agent.id, text
        except Exception as exc:  # noqa: BLE001
            logger.error("エージェント %s 呼び出し失敗: %s", agent.id, exc)
            return agent.id, exc


# ────────────────────────────────────────────────────────────────────
# Phase 3.5：Queue 多重化による並列ストリーミング（Opus 設計諮問 Q1 確定）
# standard / deep モードのエージェントラウンドで使用。
# speed モードは引き続き _call_agent() + asyncio.as_completed() を使用。
# ────────────────────────────────────────────────────────────────────

async def _stream_agents_parallel(
    agents: list[str],
    prompt: str,
    round_num: int,
    client: anthropic.AsyncAnthropic,
    model: str,
    sem: asyncio.Semaphore,
    round_results: dict[str, str],
    token_tracker: list[tuple[str, int, int]],
) -> AsyncGenerator[DebateEvent, None]:
    """
    複数エージェントを並列ストリーミングし、完了順に DebateEvent を yield する。

    Queue 多重化方式（Opus 設計諮問 Q1 案C）:
      - 各エージェントを asyncio.create_task で起動
      - 各タスクが delta イベントを queue に put
      - 本 generator が queue から drain して yield
      - タスク完了時に None（センチネル）を put して pending カウントを減算

    副作用:
      round_results[agent_id] = full_text  (成功したエージェントのみ)
      token_tracker.append((model, in_tok, out_tok))

    Args:
        agents:        呼び出すエージェント ID のリスト
        prompt:        ユーザープロンプト
        round_num:     ラウンド番号（1 or 2）
        client:        AsyncAnthropic インスタンス
        model:         使用モデル
        sem:           同時実行数制限セマフォ
        round_results: 成功したエージェントのフルテキストを格納する dict（out-param）
        token_tracker: (model, input_tokens, output_tokens) を追記するリスト（out-param）
    """
    queue: asyncio.Queue[DebateEvent | None] = asyncio.Queue()

    async def agent_task(agent_id: str) -> None:
        """1体のエージェントをストリーミング呼び出しし、queue に delta を push する。"""
        full_text = ""
        in_tok = 0
        out_tok = 0
        agent_spec = AGENTS[agent_id]

        async def _do_stream() -> None:
            nonlocal full_text, in_tok, out_tok
            async with client.messages.stream(
                model=model,
                max_tokens=MAX_TOKENS_DEBATE,
                temperature=agent_spec.temperature,
                system=agent_spec.system_prompt,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text_chunk in stream.text_stream:
                    await queue.put(AgentContentDeltaEvent(
                        round=round_num,
                        agent_id=agent_id,
                        text_chunk=text_chunk,
                    ))
                    full_text += text_chunk
                final = await stream.get_final_message()
                in_tok = final.usage.input_tokens
                out_tok = final.usage.output_tokens

        try:
            async with sem:
                await asyncio.wait_for(_do_stream(), timeout=_TIMEOUT_SECONDS)
            round_results[agent_id] = full_text
            token_tracker.append((model, in_tok, out_tok))
            await queue.put(AgentDoneEvent(
                round=round_num, agent_id=agent_id, content=full_text,
            ))
        except asyncio.TimeoutError:
            logger.error(
                "エージェント %s タイムアウト（Round %d, %.0fs 超過）— 消費トークン未計上",
                agent_id, round_num, _TIMEOUT_SECONDS,
            )
            await queue.put(AgentErrorEvent(
                round=round_num, agent_id=agent_id, error="タイムアウト",
            ))
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "エージェント %s Round %d 失敗: %s — 消費トークン未計上", agent_id, round_num, exc,
            )
            await queue.put(AgentErrorEvent(
                round=round_num, agent_id=agent_id, error=str(exc),
            ))
        finally:
            await queue.put(None)  # センチネル：このタスクの完了を通知

    # 全タスクを並列起動
    tasks = [asyncio.create_task(agent_task(aid)) for aid in agents]
    pending = len(tasks)

    # センチネルが pending 個届くまで queue を drain して yield
    # try/finally：generator が aclose() された場合（SSE 切断・例外等）も確実に後始末
    # これがないと起動済み API 呼び出しが orphan task として残りリソースリークが生じる
    try:
        while pending > 0:
            evt = await queue.get()
            if evt is None:
                pending -= 1
            else:
                yield evt
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ── プロンプト構築関数 ──────────────────────────────────────────────

def _anonymize_summary(text: str) -> str:
    """Round 1 要約のエージェントID見出しを匿名ラベルに置換する。
    Round 2 エージェントが発言者の役職を意識せず内容のみで評価するよう設計（合意バイアス防止）。
    """
    for agent_id, label in _ANON_LABELS.items():
        text = text.replace(f"## {agent_id}", f"## {label}")
    return text


def _build_round1_prompt(question: str, memory_context: str) -> str:
    """Round 1 ユーザープロンプト — 完全独立・相互参照なし"""
    ctx_section = f"\n\n## 過去の文脈\n{memory_context}" if memory_context.strip() else ""
    return (
        "以下の経営課題に対して、あなたの専門的視点からのみ独立して分析し意見を述べよ。\n"
        "他のエージェントの意見は一切参照しないこと。\n"
        f"\n## 経営課題\n{question}"
        f"{ctx_section}"
    )


def _build_round2_prompt(
    question: str,
    summary_r1: str,
    memory_context: str,
) -> str:
    """
    Round 2 ユーザープロンプト — 反論義務テンプレート（仕様書 §4.2）。
    全員一致への構造的注意喚起を常時含む。
    """
    ctx_section = f"\n\n## 過去の文脈\n{memory_context}" if memory_context.strip() else ""
    return (
        "Round 1 の熟議結果を踏まえ、意見を更新せよ。\n"
        f"\n## 経営課題\n{question}"
        f"\n\n## Round 1 の要約\n{summary_r1}"
        f"{ctx_section}"
        "\n\n## 必須タスク（仕様書 §4.2 — 全3項目を必ず実行すること）\n"
        "1. 他のエージェントの意見で「最も危険な前提」を1つ特定し、具体的に反論せよ\n"
        "2. 自分の Round 1 意見で「最も弱い部分」を1つ自己批判せよ\n"
        "3. 修正した自分の立場を明確に述べよ\n"
        "\n※ 全員一致が生じた場合は、それ自体がリスクシグナルである。"
        "なぜ全員が同じ結論に至ったのか、構造的原因を分析せよ。"
    )


def _build_synthesis_prompt(
    question: str,
    summary_r1: str,
    summary_r2: str,
    consensus_risk: bool,
) -> str:
    """Round 3 統合プロンプト — JSON出力を要求（設計書 §5.4）"""
    consensus_note = (
        "\n\n## ⚠️ 全員一致アラート（合意リスク）\n"
        "Round 2 において全員一致傾向が検出されました。\n"
        "「なぜ全員が同じ結論に至ったのか」の構造的原因を result.rationale に明記し、"
        "その上で真に第三の解を導いてください。"
        if consensus_risk
        else ""
    )
    return (
        "以下の熟議結果を踏まえ、「第三の解」を厳密に JSON 形式のみで出力せよ。\n"
        "JSON の前後に説明文・コードブロック記号を一切付けないこと。\n"
        f"\n## 元の課題\n{question}"
        f"\n\n## Round 1 要約\n{summary_r1}"
        f"\n\n## Round 2 要約\n{summary_r2}"
        f"{consensus_note}"
        "\n\n## 品質基準（仕様書 §4.3 — 5条すべてを満たすこと）\n"
        "1. 新規性：Round 1 で誰も提案していない要素を含むこと\n"
        "2. 統合性：少なくとも3つのエージェントの論点を取り込むこと\n"
        "3. 実行可能性：具体的な次のアクションを明記すること\n"
        "4. リスク認識：主要リスクとその緩和策を含むこと\n"
        "5. 時間軸：短期（3ヶ月）と中期（1〜3年）を区別すること\n"
        "\n## 出力フォーマット（このJSONのみ出力せよ）\n"
        "{\n"
        '  "conclusion": "断定形1文（〇〇すべきである。なぜなら〇〇が干渉して第三の道が生まれたから）",\n'
        '  "rationale": [\n'
        '    {"agent": "エージェントID", "point": "論点要約"}\n'
        "  ],\n"
        '  "actions_short_term": ["3ヶ月以内の具体的アクション"],\n'
        '  "actions_mid_term": ["1〜3年のマイルストーン"],\n'
        '  "assumptions": ["この結論が崩れる前提条件"],\n'
        '  "minority_view": "少数意見の要約（なければ null）",\n'
        '  "consensus_risk": false,\n'
        '  "guilford_scores": {"fluency": 1〜5整数, "flexibility": 1〜5整数, '
        '"originality": 1〜5整数, "elaboration": 1〜5整数}\n'
        "}"
    )


def _build_summary_prompt(
    agent_responses: list[tuple[str, str]],
    question: str,
) -> str:
    """Haiku 要約プロンプト"""
    responses_text = "\n\n".join(
        f"## {agent_id}\n{text}" for agent_id, text in agent_responses
    )
    return (
        "以下の経営課題に対するエージェント発言を要約せよ。\n"
        "各エージェントの立場と主要論点を保持し、1500 tokens 以内で簡潔にまとめること。\n"
        f"\n## 経営課題\n{question}"
        f"\n\n## エージェント発言\n{responses_text}"
    )


def _build_pre_mortem_prompt(conclusion: str) -> str:
    """Pre-mortem プロンプト（設計書 §9.3）"""
    return (
        "以下の経営判断が1年後に大失敗していました（Pre-mortem）。\n"
        f"\n{conclusion}\n"
        "\n失敗の原因として最もありえるものを5つ、具体的かつ端的に列挙してください。\n"
        "厳密に JSON 配列のみで出力せよ（前後の説明文は一切不要）：\n"
        '["原因1", "原因2", "原因3", "原因4", "原因5"]'
    )


# ── JSON パーサー ─────────────────────────────────────────────────

def _extract_text(message: anthropic.types.Message) -> str:
    """
    Message.content から最初の TextBlock のテキストを返す。
    anthropic SDK の ContentBlock は多型（TextBlock / ThinkingBlock 等）のため、
    isinstance チェックで型を絞り込む（設計書 §5.1 参照）。
    """
    for block in message.content:
        if isinstance(block, TextBlock):
            return block.text
    return ""


def _repair_json(text: str) -> str:
    """
    LLM出力JSONの文字列値内にある未エスケープ制御文字を修正する。
    改行・タブ・CR が JSON 文字列値の中に生で入り込むと json.loads が失敗するため、
    ステートマシンで in_string を追跡しながら \\n / \\r / \\t に置換する。
    """
    result: list[str] = []
    in_string = False
    i = 0
    while i < len(text):
        c = text[i]
        if in_string:
            if c == "\\" and i + 1 < len(text):
                result.append(c)
                result.append(text[i + 1])
                i += 2
                continue
            elif c == '"':
                in_string = False
                result.append(c)
            elif c == "\n":
                result.append("\\n")
            elif c == "\r":
                result.append("\\r")
            elif c == "\t":
                result.append("\\t")
            else:
                result.append(c)
        else:
            if c == '"':
                in_string = True
                result.append(c)
            else:
                result.append(c)
        i += 1
    return "".join(result)


def _extract_json(text: str) -> str:
    """
    レスポンステキストから JSON 文字列を抽出する。
    ```json ... ``` / ``` ... ``` のコードブロックを剥がし、
    裸の { ... } や [ ... ] にも対応する。
    """
    # コードブロック優先
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        return m.group(1).strip()
    # JSON オブジェクト
    obj = re.search(r"\{[\s\S]+\}", text)
    if obj:
        return obj.group(0)
    # JSON 配列
    arr = re.search(r"\[[\s\S]+\]", text)
    if arr:
        return arr.group(0)
    return text.strip()


def _parse_synthesis(raw: str) -> ThirdSolution:
    """
    Round 3 出力を ThirdSolution に変換する。
    JSON 解析失敗時はフォールバック（conclusion のみ）で続行する。
    """
    try:
        extracted = _extract_json(raw)
        try:
            data = json.loads(extracted)
        except json.JSONDecodeError:
            data = json.loads(_repair_json(extracted))
        return ThirdSolution(
            conclusion=str(data.get("conclusion", "")),
            rationale=[
                item for item in data.get("rationale", [])
                if isinstance(item, dict)
            ],
            actions_short_term=[str(a) for a in data.get("actions_short_term", [])],
            actions_mid_term=[str(a) for a in data.get("actions_mid_term", [])],
            assumptions=[str(a) for a in data.get("assumptions", [])],
            minority_view=(
                str(data["minority_view"])
                if data.get("minority_view") is not None
                else None
            ),
            consensus_risk=bool(data.get("consensus_risk", False)),
            guilford_scores=data.get("guilford_scores"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "Round 3 JSON 解析失敗（フォールバックで ThirdSolution を生成）: %s\nraw=%s",
            exc,
            raw[:300],
        )
        return ThirdSolution(conclusion=raw[:500] if raw else "（統合エラー）")


def _parse_pre_mortem(raw: str) -> list[str]:
    """
    Pre-mortem 出力を失敗シナリオリストに変換する。
    JSON 解析失敗時は空リストを返す（エラー停止させない）。
    """
    try:
        scenarios = json.loads(_extract_json(raw))
        if isinstance(scenarios, list):
            return [str(s) for s in scenarios[:5]]
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Pre-mortem JSON 解析失敗（空リストで続行）: %s\nraw=%s", exc, raw[:200])
    return []


# ────────────────────────────────────────────────────────────────────
# メイン API
# ────────────────────────────────────────────────────────────────────

async def run_debate(
    question: str,
    memory_context: str = "",
    mode: Mode = "standard",
    *,
    client: anthropic.AsyncAnthropic | None = None,
) -> DebateResult:
    """
    ARIFの中核関数。3ラウンド熟議を実行し DebateResult を返す。

    Args:
        question:       経営課題テキスト（空不可）
        memory_context: 過去議論・会社情報の文脈注入テキスト（任意）
        mode:           "speed" / "standard" / "deep"（既定: "standard"）
        client:         AsyncAnthropic インスタンス（省略時は自動生成）

    Returns:
        DebateResult（synthesis に ThirdSolution が格納される）

    Raises:
        ValueError:    question が空の場合、または mode が不正の場合
        RuntimeError:  成功エージェント数が MIN_AGENTS_FOR_SYNTHESIS (3) 未満の場合
    """
    # ── 0. バリデーション ───────────────────────────────────────────
    if not question.strip():
        raise ValueError("question が空です。経営課題を入力してください。")
    if mode not in ("speed", "standard", "deep"):
        raise ValueError(
            f"mode が不正: {mode!r}。'speed' / 'standard' / 'deep' のいずれかを指定してください。"
        )

    cfg = _MODE_CONFIG[mode]
    debate_model: str = cfg["debate_model"]      # type: ignore[assignment]
    summary_model: str = cfg["summary_model"]    # type: ignore[assignment]
    synthesis_model: str = cfg["synthesis_model"] # type: ignore[assignment]
    do_pre_mortem: bool = cfg["pre_mortem"]       # type: ignore[assignment]

    _client = client or anthropic.AsyncAnthropic()
    sem = asyncio.Semaphore(_SEMAPHORE_LIMIT)
    roster = get_roster()

    result = DebateResult(
        question=question,
        memory_context=memory_context,
        mode=mode,
    )
    start_time = time.monotonic()

    # ── Round 1：全エージェントを完全並列呼び出し ──────────────────────
    logger.info("[Round 1] 開始 — %d 体並列 (model=%s)", len(roster), debate_model)
    r1_prompt = _build_round1_prompt(question, memory_context)
    r1_tasks = [
        _call_agent(AGENTS[aid], r1_prompt, _client, debate_model, sem)
        for aid in roster
    ]
    r1_raw = await asyncio.gather(*r1_tasks, return_exceptions=True)

    for item in r1_raw:
        if isinstance(item, BaseException):
            logger.error("Round 1 gather 例外: %s", item)
            continue
        agent_id, response = item
        if isinstance(response, BaseException):
            logger.warning("エージェント %s Round 1 スキップ: %s", agent_id, response)
        else:
            result.round1[agent_id] = response

    surviving = len(result.round1)
    if surviving < MIN_AGENTS_FOR_SYNTHESIS:
        raise RuntimeError(
            f"Round 1 成功エージェント数 {surviving} 体は"
            f"最低要件 {MIN_AGENTS_FOR_SYNTHESIS} 体を下回ります。"
        )
    logger.info("[Round 1] 完了 — %d 体成功", surviving)

    # 多様性スコア計算（設計書 §9.1）
    result.diversity_score_r1 = diversity_score(list(result.round1.values()))
    if result.diversity_score_r1 < 0.3:
        logger.warning(
            "多様性スコア低警告 (Round 1): Φ=%.3f < 0.3 — プロンプト見直しを推奨",
            result.diversity_score_r1,
        )

    # Round 1 → 要約（Haiku）
    logger.info("[Summary R1] 開始 (model=%s)", summary_model)
    summary_r1_msg = await _call_with_retry(
        _client,
        model=summary_model,
        max_tokens=MAX_TOKENS_SUMMARY,
        messages=[{
            "role": "user",
            "content": _build_summary_prompt(list(result.round1.items()), question),
        }],
    )
    summary_r1: str = _anonymize_summary(_extract_text(summary_r1_msg))

    # ── Round 2：生存エージェントのみ並列呼び出し（反論義務プロンプト） ──
    surviving_agents = [aid for aid in roster if aid in result.round1]
    logger.info("[Round 2] 開始 — %d 体並列 (model=%s)", len(surviving_agents), debate_model)
    r2_prompt = _build_round2_prompt(question, summary_r1, memory_context)
    r2_tasks = [
        _call_agent(AGENTS[aid], r2_prompt, _client, debate_model, sem)
        for aid in surviving_agents
    ]
    r2_raw = await asyncio.gather(*r2_tasks, return_exceptions=True)

    for item in r2_raw:
        if isinstance(item, BaseException):
            logger.error("Round 2 gather 例外: %s", item)
            continue
        agent_id, response = item
        if isinstance(response, BaseException):
            logger.warning("エージェント %s Round 2 スキップ: %s", agent_id, response)
        else:
            result.round2[agent_id] = response

    # Round 2 多様性スコア + 収束チェック（設計書 §9.2）
    if result.round2:
        result.diversity_score_r2 = diversity_score(list(result.round2.values()))
        result.consensus_risk = convergence_check(list(result.round2.values()))
        if result.consensus_risk:
            logger.warning(
                "収束チェック: 全員一致リスク検出 (Φ=%.3f) — Round 3 に構造的原因分析を追加",
                result.diversity_score_r2,
            )

    # Round 2 → 要約（Haiku）
    logger.info("[Summary R2] 開始 (model=%s)", summary_model)
    r2_items = list(result.round2.items()) if result.round2 else list(result.round1.items())
    summary_r2_msg = await _call_with_retry(
        _client,
        model=summary_model,
        max_tokens=MAX_TOKENS_SUMMARY,
        messages=[{
            "role": "user",
            "content": _build_summary_prompt(r2_items, question),
        }],
    )
    summary_r2: str = _extract_text(summary_r2_msg)

    # ── Round 3：統合プロンプト → 第三の解 ───────────────────────────
    logger.info("[Round 3] 統合開始 (model=%s)", synthesis_model)
    synthesis_msg = await asyncio.wait_for(
        _call_with_retry(
            _client,
            model=synthesis_model,
            max_tokens=MAX_TOKENS_DEBATE,
            temperature=0.5,   # 統合者の温度（設計書 §4.2 synthesizer = 0.5）
            messages=[{
                "role": "user",
                "content": _build_synthesis_prompt(
                    question, summary_r1, summary_r2, result.consensus_risk
                ),
            }],
        ),
        timeout=_TIMEOUT_SECONDS,
    )
    synthesis = _parse_synthesis(_extract_text(synthesis_msg))
    synthesis.consensus_risk = result.consensus_risk  # DebateResult と同期

    # ── Pre-mortem：常足・熟考モードのみ（設計書 §9.3） ──────────────
    if do_pre_mortem and synthesis.conclusion:
        logger.info("[Pre-mortem] 開始 (model=%s)", synthesis_model)
        pm_msg = await asyncio.wait_for(
            _call_with_retry(
                _client,
                model=synthesis_model,
                max_tokens=MAX_TOKENS_SUMMARY,
                temperature=0.7,
                messages=[{
                    "role": "user",
                    "content": _build_pre_mortem_prompt(synthesis.conclusion),
                }],
            ),
            timeout=_TIMEOUT_SECONDS,
        )
        synthesis.failure_scenarios = _parse_pre_mortem(_extract_text(pm_msg))

    result.synthesis = synthesis
    result.duration_seconds = time.monotonic() - start_time

    logger.info(
        "[run_debate] 完了 — mode=%s, duration=%.1fs, round1=%d体, round2=%d体",
        mode,
        result.duration_seconds,
        len(result.round1),
        len(result.round2),
    )

    return result


# ────────────────────────────────────────────────────────────────────
# stream_debate() — ストリーミング版（Phase 1B）
# ────────────────────────────────────────────────────────────────────

async def stream_debate(
    question: str,
    memory_context: str = "",
    mode: Mode = "standard",
    *,
    client: anthropic.AsyncAnthropic | None = None,
) -> AsyncIterator[DebateEvent]:
    """
    ARIFの中核関数（ストリーミング版 — Phase 1B）。
    熟議の進行に合わせて DebateEvent を逐次 yield する。

    run_debate() との違い:
      - asyncio.as_completed により各エージェントの完了を完了順に yield
      - 呼び出し側は async for で進行状況をリアルタイム受信できる
      - FastAPI SSE / CLI 進行表示 / WebSocket 配信に直結可能

    Yields（設計書 §7.3 イベント順序）:
        SessionStartEvent   — セッション開始（1件）
        RoundStartEvent     — Round 1 / 2 の開始（各1件）
        AgentDoneEvent      — 各エージェント完了（完了順・最大10件）
        RoundSummaryEvent   — ラウンド要約・Φスコア（各1件）
        SynthesisDoneEvent  — 第三の解完成（1件）
        PreMortemDoneEvent  — Pre-mortem完了（standard/deep のみ）
        CompleteEvent       — 熟議完了（1件）

    Args:
        question:       経営課題テキスト（空不可）
        memory_context: 過去議論・会社情報の文脈注入テキスト（任意）
        mode:           "speed" / "standard" / "deep"（既定: "standard"）
        client:         AsyncAnthropic インスタンス（省略時は自動生成）

    Raises:
        ValueError:    question が空の場合、または mode が不正の場合
        RuntimeError:  成功エージェント数が MIN_AGENTS_FOR_SYNTHESIS 未満の場合
    """
    # ── 0. バリデーション ───────────────────────────────────────────
    if not question.strip():
        raise ValueError("question が空です。経営課題を入力してください。")
    if mode not in ("speed", "standard", "deep"):
        raise ValueError(
            f"mode が不正: {mode!r}。'speed' / 'standard' / 'deep' のいずれかを指定してください。"
        )

    cfg = _MODE_CONFIG[mode]
    debate_model: str = cfg["debate_model"]       # type: ignore[assignment]
    summary_model: str = cfg["summary_model"]     # type: ignore[assignment]
    synthesis_model: str = cfg["synthesis_model"] # type: ignore[assignment]
    do_pre_mortem: bool = cfg["pre_mortem"]        # type: ignore[assignment]

    _client = client or anthropic.AsyncAnthropic()
    sem = asyncio.Semaphore(_SEMAPHORE_LIMIT)
    roster = get_roster()
    start_time = time.monotonic()

    # Phase 3.5：standard / deep はストリーミング。speed はバッチ（既存動作）
    is_streaming = mode != "speed"

    # (model, input_tokens, output_tokens) のリスト。CompleteEvent.total_cost_usd 算出に使用
    token_tracker: list[tuple[str, int, int]] = []

    yield SessionStartEvent(mode=mode)

    # ── Round 1 ────────────────────────────────────────────────────
    yield RoundStartEvent(round=1, agents=list(roster))
    r1_prompt = _build_round1_prompt(question, memory_context)
    round1: dict[str, str] = {}

    if is_streaming:
        # standard / deep：Queue 多重化ストリーミング（Opus 設計諮問 Q1 案C）
        async for evt in _stream_agents_parallel(
            agents=list(roster),
            prompt=r1_prompt,
            round_num=1,
            client=_client,
            model=debate_model,
            sem=sem,
            round_results=round1,
            token_tracker=token_tracker,
        ):
            yield evt
    else:
        # speed：バッチ（F-010 対象外・30秒制約 優先）
        r1_coros = [
            _call_agent(AGENTS[aid], r1_prompt, _client, debate_model, sem)
            for aid in roster
        ]
        for done in asyncio.as_completed(r1_coros):
            try:
                agent_id, response = await done
                if not isinstance(response, Exception):
                    round1[agent_id] = response
                    yield AgentDoneEvent(round=1, agent_id=agent_id, content=response)
                else:
                    logger.warning("エージェント %s Round 1 スキップ: %s", agent_id, response)
            except Exception as exc:  # noqa: BLE001
                logger.error("Round 1 as_completed 例外: %s", exc)

    if len(round1) < MIN_AGENTS_FOR_SYNTHESIS:
        raise RuntimeError(
            f"Round 1 成功エージェント数 {len(round1)} 体は"
            f"最低要件 {MIN_AGENTS_FOR_SYNTHESIS} 体を下回ります。"
        )

    diversity_r1 = diversity_score(list(round1.values()))
    if diversity_r1 < 0.3:
        logger.warning("多様性スコア低警告 (Round 1): Φ=%.3f < 0.3", diversity_r1)
    yield RoundSummaryEvent(round=1, diversity_score_phi=diversity_r1, consensus_risk=False)

    # Round 1 → 要約（全モード Haiku バッチ）
    summary_r1_msg = await _call_with_retry(
        _client,
        model=summary_model,
        max_tokens=MAX_TOKENS_SUMMARY,
        messages=[{
            "role": "user",
            "content": _build_summary_prompt(list(round1.items()), question),
        }],
    )
    summary_r1 = _anonymize_summary(_extract_text(summary_r1_msg))
    token_tracker.append((
        summary_model, summary_r1_msg.usage.input_tokens, summary_r1_msg.usage.output_tokens,
    ))

    # ── Round 2 ────────────────────────────────────────────────────
    surviving_agents = [aid for aid in roster if aid in round1]
    yield RoundStartEvent(round=2, agents=surviving_agents)
    r2_prompt = _build_round2_prompt(question, summary_r1, memory_context)
    round2: dict[str, str] = {}

    if is_streaming:
        async for evt in _stream_agents_parallel(
            agents=surviving_agents,
            prompt=r2_prompt,
            round_num=2,
            client=_client,
            model=debate_model,
            sem=sem,
            round_results=round2,
            token_tracker=token_tracker,
        ):
            yield evt
    else:
        r2_coros = [
            _call_agent(AGENTS[aid], r2_prompt, _client, debate_model, sem)
            for aid in surviving_agents
        ]
        for done in asyncio.as_completed(r2_coros):
            try:
                agent_id, response = await done
                if not isinstance(response, Exception):
                    round2[agent_id] = response
                    yield AgentDoneEvent(round=2, agent_id=agent_id, content=response)
                else:
                    logger.warning("エージェント %s Round 2 スキップ: %s", agent_id, response)
            except Exception as exc:  # noqa: BLE001
                logger.error("Round 2 as_completed 例外: %s", exc)

    diversity_r2 = diversity_score(list(round2.values())) if round2 else 0.0
    consensus_risk = convergence_check(list(round2.values())) if round2 else False
    if consensus_risk:
        logger.warning("収束チェック: 全員一致リスク検出 (Φ=%.3f)", diversity_r2)
    yield RoundSummaryEvent(round=2, diversity_score_phi=diversity_r2, consensus_risk=consensus_risk)

    # Round 2 → 要約（全モード Haiku バッチ）
    r2_items = list(round2.items()) if round2 else list(round1.items())
    summary_r2_msg = await _call_with_retry(
        _client,
        model=summary_model,
        max_tokens=MAX_TOKENS_SUMMARY,
        messages=[{
            "role": "user",
            "content": _build_summary_prompt(r2_items, question),
        }],
    )
    summary_r2 = _extract_text(summary_r2_msg)
    token_tracker.append((
        summary_model, summary_r2_msg.usage.input_tokens, summary_r2_msg.usage.output_tokens,
    ))

    # ── Round 3：統合プロンプト → 第三の解 ─────────────────────────
    synthesis_prompt = _build_synthesis_prompt(question, summary_r1, summary_r2, consensus_risk)

    if is_streaming:
        # standard / deep：Synthesis もストリーミング（SynthesisDeltaEvent を yield）
        # asyncio.timeout() で接続ハング含む全体をカバー（Python 3.12）
        synthesis_text = ""
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            async with _client.messages.stream(
                model=synthesis_model,
                max_tokens=MAX_TOKENS_DEBATE,
                temperature=0.5,
                messages=[{"role": "user", "content": synthesis_prompt}],
            ) as stream:
                async for text_chunk in stream.text_stream:
                    yield SynthesisDeltaEvent(text_chunk=text_chunk)
                    synthesis_text += text_chunk
                synthesis_final = await stream.get_final_message()
        token_tracker.append((
            synthesis_model,
            synthesis_final.usage.input_tokens,
            synthesis_final.usage.output_tokens,
        ))
        synthesis = _parse_synthesis(synthesis_text)
    else:
        # speed：バッチ（コストは summary + synthesis のみ追跡。エージェント個別は Haiku ゆえ省略）
        synthesis_msg = await asyncio.wait_for(
            _call_with_retry(
                _client,
                model=synthesis_model,
                max_tokens=MAX_TOKENS_DEBATE,
                temperature=0.5,
                messages=[{"role": "user", "content": synthesis_prompt}],
            ),
            timeout=_TIMEOUT_SECONDS,
        )
        synthesis = _parse_synthesis(_extract_text(synthesis_msg))
        token_tracker.append((
            synthesis_model, synthesis_msg.usage.input_tokens, synthesis_msg.usage.output_tokens,
        ))

    synthesis.consensus_risk = consensus_risk
    yield SynthesisDoneEvent(synthesis=synthesis)

    # ── Pre-mortem：常足・熟考モードのみ（全モード バッチ呼び出し）────
    if do_pre_mortem and synthesis.conclusion:
        pm_msg = await asyncio.wait_for(
            _call_with_retry(
                _client,
                model=synthesis_model,
                max_tokens=MAX_TOKENS_SUMMARY,
                temperature=0.7,
                messages=[{
                    "role": "user",
                    "content": _build_pre_mortem_prompt(synthesis.conclusion),
                }],
            ),
            timeout=_TIMEOUT_SECONDS,
        )
        failure_scenarios = _parse_pre_mortem(_extract_text(pm_msg))
        synthesis.failure_scenarios = failure_scenarios
        token_tracker.append((
            synthesis_model, pm_msg.usage.input_tokens, pm_msg.usage.output_tokens,
        ))
        yield PreMortemDoneEvent(failure_scenarios=failure_scenarios)

    # ── 完了：コスト算出 ─────────────────────────────────────────────
    cost_usd = float(total_cost(token_tracker))
    yield CompleteEvent(
        duration_seconds=time.monotonic() - start_time,
        total_cost_usd=cost_usd,
    )
