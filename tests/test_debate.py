"""
tests/test_debate.py — debate.py 機能テスト（Phase 1A / 1B）

テスト構成:
  TestThirdSolution        — データクラス不変条件（仕様書 §4.4・F-015）
  TestDebateResult         — コンテナ構造テスト
  TestRunDebateValidation  — 入力バリデーション
  TestRunDebateStandardMode — 標準モード E2E（モック API）
  TestRunDebateSpeedMode   — 早足モード E2E（Pre-mortem なし確認）
  TestRunDebateDeepMode    — 熟考モード E2E
  TestRunDebateResiliency  — エラー耐性（全エージェント失敗 → RuntimeError）
  TestDiversityAndConvergence — 品質指標フィールドの型・範囲テスト
  TestMinAgentsConstant    — MIN_AGENTS_FOR_SYNTHESIS 定数テスト
  TestStreamDebate         — stream_debate() イベント列テスト（Phase 1B）

方針（Opus 審査 2026-05-27 指摘事項）:
  - プロンプト本文の完全一致テストは禁忌（将来の改善反復を阻害するため）
  - 構造・型・範囲・不変条件のみを検証する
"""

from __future__ import annotations

import pytest

from engine.debate import (
    ARIF_DISCLAIMER,
    MIN_AGENTS_FOR_SYNTHESIS,
    _ANON_LABELS,
    _anonymize_summary,
    AgentContentDeltaEvent,
    AgentDoneEvent,
    AgentErrorEvent,
    CompleteEvent,
    DebateResult,
    PreMortemDoneEvent,
    RoundStartEvent,
    RoundSummaryEvent,
    SessionStartEvent,
    SynthesisDeltaEvent,
    SynthesisDoneEvent,
    ThirdSolution,
    run_debate,
    stream_debate,
)
from engine.pricing import MODEL_PRICES, compute_cost, total_cost


# ───────────────────────────────────────
# ThirdSolution データクラスの不変条件テスト
# （仕様書 §4.4・設計書 §5.4）
# ───────────────────────────────────────

class TestThirdSolution:
    """ThirdSolution の不変条件を検証する"""

    def test_disclaimer_exact_match(self):
        """免責文言は仕様書 F-015 と完全一致すること（固定文・改変禁止）"""
        sol = ThirdSolution()
        # 仕様書 F-015・§4.4 と完全一致の確認
        assert sol.disclaimer == ARIF_DISCLAIMER
        assert sol.disclaimer == (
            "本提案は PRISM が熟議を経て導出した最善の一手です。"
            "最終判断は社長が下されるものです。"
        )

    def test_disclaimer_modification_rejected(self):
        """免責文言を改変した ThirdSolution は ValueError を投げること（F-015 防壁）"""
        with pytest.raises(ValueError, match="改変禁止"):
            ThirdSolution(disclaimer="勝手な免責文")

    def test_disclaimer_empty_rejected(self):
        """空の免責文言も拒否されること"""
        with pytest.raises(ValueError, match="改変禁止"):
            ThirdSolution(disclaimer="")

    def test_defaults_are_safe(self):
        """デフォルト値がすべて安全な空値であること"""
        sol = ThirdSolution()
        assert sol.conclusion == ""
        assert sol.rationale == []
        assert sol.actions_short_term == []
        assert sol.actions_mid_term == []
        assert sol.assumptions == []
        assert sol.minority_view is None
        assert sol.consensus_risk is False
        assert sol.failure_scenarios == []
        assert sol.guilford_scores is None

    def test_early_mode_has_no_failure_scenarios(self):
        """早足モードの Pre-mortem は空リスト（設計書 §9.3）"""
        sol = ThirdSolution(conclusion="テスト結論")
        # 早足モードでは failure_scenarios に何も追加しない
        assert sol.failure_scenarios == []

    def test_standard_mode_structure(self):
        """常足モードの構造サンプル：必須フィールドが揃うこと"""
        sol = ThirdSolution(
            conclusion="OEM依存を断ち切り自社ブランドへ集中すべきである。",
            rationale=[
                {"agent": "strategist", "point": "5年後のポジショニングに自社ブランドが不可欠"},
                {"agent": "cfo",        "point": "OEM単価下落でROIが2年後に逆転"},
                {"agent": "market",     "point": "顧客調査でブランド認知欲求が確認された"},
            ],
            actions_short_term=["自社ブランドロードマップ策定", "OEM契約更新の一時停止"],
            actions_mid_term=["2027年Q1 自社製品ライン3本稼働"],
            assumptions=["競合がブランド転換を同時実施しないこと"],
            failure_scenarios=[
                "既存OEM顧客が一斉離反する",
                "ブランド構築コストが予算の3倍に膨らむ",
                "技術人員が自社ブランド開発についていけない",
                "市場が成熟しブランド転換の旨味がなくなる",
                "社長判断がチームに伝わらず現場が混乱する",
            ],
            guilford_scores={"fluency": 4, "flexibility": 5, "originality": 4, "elaboration": 5},
        )
        # 品質基準：3エージェント以上の論拠（仕様書 §4.3 統合性）
        assert len(sol.rationale) >= 3
        # 品質基準：短期・中期アクション双方あること（仕様書 §4.3 時間軸）
        assert len(sol.actions_short_term) >= 1
        assert len(sol.actions_mid_term) >= 1
        # Pre-mortem は5つ（仕様書 F-006）
        assert len(sol.failure_scenarios) == 5
        # Guilford 4次元すべてが揃うこと（任意フィールドだが、構造例として）
        assert set(sol.guilford_scores.keys()) == {
            "fluency", "flexibility", "originality", "elaboration",
        }
        # 免責文言は固定文と完全一致（F-015）
        assert sol.disclaimer == ARIF_DISCLAIMER

    def test_consensus_risk_flag(self):
        """全員一致時は consensus_risk=True がセットされること"""
        sol = ThirdSolution(consensus_risk=True)
        assert sol.consensus_risk is True

    def test_rationale_dict_structure(self):
        """rationale の各要素は agent/point キーを持つ dict であること"""
        sol = ThirdSolution(
            rationale=[
                {"agent": "strategist", "point": "テスト論点1"},
                {"agent": "cfo",        "point": "テスト論点2"},
            ],
        )
        for entry in sol.rationale:
            assert "agent" in entry
            assert "point" in entry
            assert isinstance(entry["agent"], str)
            assert isinstance(entry["point"], str)

    def test_guilford_scores_optional(self):
        """guilford_scores は任意フィールドで、None でも可"""
        sol_with = ThirdSolution(
            guilford_scores={"fluency": 3, "flexibility": 4, "originality": 5, "elaboration": 4},
        )
        sol_without = ThirdSolution()
        assert sol_with.guilford_scores is not None
        assert sol_without.guilford_scores is None


class TestDebateResult:
    """DebateResult の不変条件を検証する"""

    def test_synthesis_accepts_third_solution(self):
        """synthesis フィールドが ThirdSolution 型を受け付けること"""
        sol = ThirdSolution(conclusion="テスト結論")
        result = DebateResult(
            question="テスト課題",
            memory_context="",
            synthesis=sol,
        )
        assert isinstance(result.synthesis, ThirdSolution)
        assert result.synthesis.conclusion == "テスト結論"

    def test_synthesis_none_by_default(self):
        """synthesis のデフォルトは None（未実行状態）"""
        result = DebateResult(question="q", memory_context="")
        assert result.synthesis is None

    def test_mode_default(self) -> None:
        """mode のデフォルトは 'standard' であること"""
        result = DebateResult(question="q", memory_context="")
        assert result.mode == "standard"

    def test_diversity_scores_default_zero(self) -> None:
        """多様性スコアのデフォルトが 0.0 であること"""
        result = DebateResult(question="q", memory_context="")
        assert result.diversity_score_r1 == 0.0
        assert result.diversity_score_r2 == 0.0

    def test_consensus_risk_default_false(self) -> None:
        """consensus_risk のデフォルトが False であること"""
        result = DebateResult(question="q", memory_context="")
        assert result.consensus_risk is False


# ────────────────────────────────────────────────────────────────────
# E2E テスト — run_debate() (モック API 使用)
# ────────────────────────────────────────────────────────────────────

class TestRunDebateValidation:
    """入力バリデーションテスト（実 API 呼び出しなし）"""

    @pytest.mark.asyncio
    async def test_empty_question_raises_value_error(self, mock_client) -> None:
        """空の question は ValueError を送出すること"""
        with pytest.raises(ValueError, match="空"):
            await run_debate("", client=mock_client)

    @pytest.mark.asyncio
    async def test_whitespace_only_question_raises_value_error(self, mock_client) -> None:
        """空白のみの question は ValueError を送出すること"""
        with pytest.raises(ValueError, match="空"):
            await run_debate("   \n\t", client=mock_client)

    @pytest.mark.asyncio
    async def test_invalid_mode_raises_value_error(self, mock_client) -> None:
        """不正な mode は ValueError を送出すること"""
        with pytest.raises(ValueError, match="mode"):
            await run_debate("テスト課題", mode="invalid", client=mock_client)  # type: ignore[arg-type]


class TestRunDebateStandardMode:
    """標準モード（standard）E2E テスト — モック API"""

    @pytest.mark.asyncio
    async def test_returns_debate_result(self, mock_client) -> None:
        """run_debate() が DebateResult を返すこと"""
        result = await run_debate("テスト経営課題", client=mock_client)
        assert isinstance(result, DebateResult)

    @pytest.mark.asyncio
    async def test_synthesis_is_third_solution(self, mock_client) -> None:
        """synthesis が ThirdSolution 型であること"""
        result = await run_debate("テスト経営課題", client=mock_client)
        assert isinstance(result.synthesis, ThirdSolution)

    @pytest.mark.asyncio
    async def test_disclaimer_is_always_correct(self, mock_client) -> None:
        """免責文言は常に F-015 固定文と一致すること"""
        result = await run_debate("テスト経営課題", client=mock_client)
        assert result.synthesis is not None
        assert result.synthesis.disclaimer == ARIF_DISCLAIMER

    @pytest.mark.asyncio
    async def test_round1_has_minimum_agents(self, mock_client) -> None:
        """round1 に MIN_AGENTS_FOR_SYNTHESIS 以上のエージェントが存在すること"""
        result = await run_debate("テスト経営課題", client=mock_client)
        assert len(result.round1) >= MIN_AGENTS_FOR_SYNTHESIS

    @pytest.mark.asyncio
    async def test_round2_has_responses(self, mock_client) -> None:
        """round2 に1体以上の発言が存在すること"""
        result = await run_debate("テスト経営課題", client=mock_client)
        assert len(result.round2) >= 1

    @pytest.mark.asyncio
    async def test_pre_mortem_filled_in_standard_mode(self, mock_client) -> None:
        """標準モードでは failure_scenarios が空でないこと（設計書 §9.3）"""
        result = await run_debate("テスト経営課題", mode="standard", client=mock_client)
        assert result.synthesis is not None
        assert isinstance(result.synthesis.failure_scenarios, list)
        assert len(result.synthesis.failure_scenarios) > 0

    @pytest.mark.asyncio
    async def test_memory_context_preserved(self, mock_client) -> None:
        """memory_context が DebateResult に保持されること"""
        ctx = "テスト用会社コンテキスト：製造業・従業員200名"
        result = await run_debate("テスト経営課題", memory_context=ctx, client=mock_client)
        assert result.memory_context == ctx

    @pytest.mark.asyncio
    async def test_question_preserved(self, mock_client) -> None:
        """question が DebateResult に保持されること"""
        q = "当社の5年後の主力事業をどう描くべきか？"
        result = await run_debate(q, client=mock_client)
        assert result.question == q

    @pytest.mark.asyncio
    async def test_mode_preserved(self, mock_client) -> None:
        """mode が DebateResult に保持されること"""
        result = await run_debate("テスト経営課題", mode="standard", client=mock_client)
        assert result.mode == "standard"

    @pytest.mark.asyncio
    async def test_duration_positive(self, mock_client) -> None:
        """duration_seconds が正の数であること"""
        result = await run_debate("テスト経営課題", client=mock_client)
        assert result.duration_seconds > 0.0

    @pytest.mark.asyncio
    async def test_round1_values_are_strings(self, mock_client) -> None:
        """round1 の各エージェント発言が文字列であること"""
        result = await run_debate("テスト経営課題", client=mock_client)
        for agent_id, text in result.round1.items():
            assert isinstance(agent_id, str)
            assert isinstance(text, str)
            assert len(text) > 0


class TestRunDebateSpeedMode:
    """早足モード（speed）E2E テスト"""

    @pytest.mark.asyncio
    async def test_speed_mode_no_pre_mortem(self, mock_client) -> None:
        """早足モードでは failure_scenarios が空リストであること（§9.3 Pre-mortem 省略）"""
        result = await run_debate("テスト経営課題", mode="speed", client=mock_client)
        assert result.synthesis is not None
        assert result.synthesis.failure_scenarios == []

    @pytest.mark.asyncio
    async def test_speed_mode_returns_third_solution(self, mock_client) -> None:
        """早足モードでも ThirdSolution が返ること"""
        result = await run_debate("テスト経営課題", mode="speed", client=mock_client)
        assert isinstance(result.synthesis, ThirdSolution)

    @pytest.mark.asyncio
    async def test_speed_mode_disclaimer_correct(self, mock_client) -> None:
        """早足モードでも免責文言は固定文と一致すること"""
        result = await run_debate("テスト経営課題", mode="speed", client=mock_client)
        assert result.synthesis is not None
        assert result.synthesis.disclaimer == ARIF_DISCLAIMER


class TestRunDebateDeepMode:
    """熟考モード（deep）E2E テスト"""

    @pytest.mark.asyncio
    async def test_deep_mode_pre_mortem_filled(self, mock_client) -> None:
        """熟考モードでは failure_scenarios が充填されること"""
        result = await run_debate("テスト経営課題", mode="deep", client=mock_client)
        assert result.synthesis is not None
        assert len(result.synthesis.failure_scenarios) > 0

    @pytest.mark.asyncio
    async def test_deep_mode_returns_debate_result(self, mock_client) -> None:
        """熟考モードでも DebateResult が返ること"""
        result = await run_debate("テスト経営課題", mode="deep", client=mock_client)
        assert isinstance(result, DebateResult)


class TestRunDebateResiliency:
    """エラー耐性テスト"""

    @pytest.mark.asyncio
    async def test_all_agents_failing_raises_runtime_error(
        self, mock_client_all_failing
    ) -> None:
        """全エージェントが失敗した場合 RuntimeError を送出すること"""
        with pytest.raises(RuntimeError, match="最低要件"):
            await run_debate("テスト経営課題", client=mock_client_all_failing)


class TestDiversityAndConvergence:
    """多様性・収束チェックフィールドのテスト"""

    @pytest.mark.asyncio
    async def test_diversity_score_r1_is_float(self, mock_client) -> None:
        """diversity_score_r1 が float であること"""
        result = await run_debate("テスト経営課題", client=mock_client)
        assert isinstance(result.diversity_score_r1, float)

    @pytest.mark.asyncio
    async def test_diversity_score_r1_in_range(self, mock_client) -> None:
        """diversity_score_r1 が [0.0, 1.0] の範囲内であること"""
        result = await run_debate("テスト経営課題", client=mock_client)
        assert 0.0 <= result.diversity_score_r1 <= 1.0

    @pytest.mark.asyncio
    async def test_diversity_score_r2_in_range(self, mock_client) -> None:
        """diversity_score_r2 が [0.0, 1.0] の範囲内であること"""
        result = await run_debate("テスト経営課題", client=mock_client)
        assert 0.0 <= result.diversity_score_r2 <= 1.0

    @pytest.mark.asyncio
    async def test_consensus_risk_is_bool(self, mock_client) -> None:
        """consensus_risk が bool であること"""
        result = await run_debate("テスト経営課題", client=mock_client)
        assert isinstance(result.consensus_risk, bool)

    @pytest.mark.asyncio
    async def test_synthesis_consensus_risk_matches_result(self, mock_client) -> None:
        """ThirdSolution.consensus_risk が DebateResult.consensus_risk と同期していること"""
        result = await run_debate("テスト経営課題", client=mock_client)
        assert result.synthesis is not None
        assert result.synthesis.consensus_risk == result.consensus_risk


class TestMinAgentsConstant:
    """MIN_AGENTS_FOR_SYNTHESIS 定数テスト"""

    def test_min_agents_is_three(self) -> None:
        """MIN_AGENTS_FOR_SYNTHESIS が 3 であること（仕様書 §5.1 設計判断）"""
        assert MIN_AGENTS_FOR_SYNTHESIS == 3

    def test_min_agents_is_positive_int(self) -> None:
        """MIN_AGENTS_FOR_SYNTHESIS が正の整数であること"""
        assert isinstance(MIN_AGENTS_FOR_SYNTHESIS, int)
        assert MIN_AGENTS_FOR_SYNTHESIS > 0


# ────────────────────────────────────────────────────────────────────
# stream_debate() テスト（Phase 1B — 設計書 §7.3 イベント仕様）
# ────────────────────────────────────────────────────────────────────

class TestStreamDebate:
    """
    stream_debate() が正しいイベント列を yield することを検証する。

    検証方針:
      - イベントの型・個数・順序（先頭/末尾）を構造的に確認
      - プロンプト文言には依存せず、不変条件のみをアサート
      - モード別の Pre-mortem 有無を確認（§9.3）
    """

    # ── 基本構造テスト ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_stream_yields_events(self, mock_client) -> None:
        """stream_debate() が最低1件以上のイベントを yield すること"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_stream_starts_with_session_start(self, mock_client) -> None:
        """最初のイベントが SessionStartEvent であること（設計書 §7.3）"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        assert isinstance(events[0], SessionStartEvent)

    @pytest.mark.asyncio
    async def test_stream_ends_with_complete(self, mock_client) -> None:
        """最後のイベントが CompleteEvent であること（設計書 §7.3）"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        assert isinstance(events[-1], CompleteEvent)

    @pytest.mark.asyncio
    async def test_stream_contains_exactly_one_synthesis_done(self, mock_client) -> None:
        """SynthesisDoneEvent がちょうど1件含まれること"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        synthesis_events = [e for e in events if isinstance(e, SynthesisDoneEvent)]
        assert len(synthesis_events) == 1

    @pytest.mark.asyncio
    async def test_stream_contains_two_round_start_events(self, mock_client) -> None:
        """RoundStartEvent が2件（Round 1・Round 2 各1件）含まれること"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        round_start_events = [e for e in events if isinstance(e, RoundStartEvent)]
        assert len(round_start_events) == 2

    @pytest.mark.asyncio
    async def test_stream_contains_two_round_summary_events(self, mock_client) -> None:
        """RoundSummaryEvent が2件（Round 1・Round 2 各1件）含まれること"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        summary_events = [e for e in events if isinstance(e, RoundSummaryEvent)]
        assert len(summary_events) == 2

    @pytest.mark.asyncio
    async def test_stream_round_summary_rounds_are_1_and_2(self, mock_client) -> None:
        """RoundSummaryEvent の round フィールドが 1 と 2 であること"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        summary_events = [e for e in events if isinstance(e, RoundSummaryEvent)]
        rounds = {e.round for e in summary_events}
        assert rounds == {1, 2}

    # ── Pre-mortem モード分岐テスト ────────────────────────────────

    @pytest.mark.asyncio
    async def test_stream_standard_has_pre_mortem_done(self, mock_client) -> None:
        """標準モードでは PreMortemDoneEvent が1件含まれること（設計書 §9.3）"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="standard", client=mock_client
        )]
        pm_events = [e for e in events if isinstance(e, PreMortemDoneEvent)]
        assert len(pm_events) == 1

    @pytest.mark.asyncio
    async def test_stream_speed_no_pre_mortem(self, mock_client) -> None:
        """早足モードでは PreMortemDoneEvent が含まれないこと（§9.3 Pre-mortem 省略）"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="speed", client=mock_client
        )]
        pm_events = [e for e in events if isinstance(e, PreMortemDoneEvent)]
        assert len(pm_events) == 0

    @pytest.mark.asyncio
    async def test_stream_deep_has_pre_mortem_done(self, mock_client) -> None:
        """熟考モードでは PreMortemDoneEvent が1件含まれること（設計書 §9.3）"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="deep", client=mock_client
        )]
        pm_events = [e for e in events if isinstance(e, PreMortemDoneEvent)]
        assert len(pm_events) == 1

    # ── 不変条件・フィールド値テスト ──────────────────────────────

    @pytest.mark.asyncio
    async def test_stream_synthesis_disclaimer_correct(self, mock_client) -> None:
        """SynthesisDoneEvent 内の disclaimer は F-015 固定文と一致すること"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        synthesis_events = [e for e in events if isinstance(e, SynthesisDoneEvent)]
        assert synthesis_events[0].synthesis.disclaimer == ARIF_DISCLAIMER

    @pytest.mark.asyncio
    async def test_stream_session_start_mode_matches(self, mock_client) -> None:
        """SessionStartEvent の mode フィールドがリクエスト mode と一致すること"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="speed", client=mock_client
        )]
        session_events = [e for e in events if isinstance(e, SessionStartEvent)]
        assert len(session_events) == 1
        assert session_events[0].mode == "speed"

    @pytest.mark.asyncio
    async def test_stream_complete_duration_positive(self, mock_client) -> None:
        """CompleteEvent の duration_seconds が正の数であること"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        complete_events = [e for e in events if isinstance(e, CompleteEvent)]
        assert len(complete_events) == 1
        assert complete_events[0].duration_seconds > 0.0

    @pytest.mark.asyncio
    async def test_stream_agent_done_round1_minimum_count(self, mock_client) -> None:
        """Round 1 の AgentDoneEvent が MIN_AGENTS_FOR_SYNTHESIS 以上であること"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        r1_done = [e for e in events if isinstance(e, AgentDoneEvent) and e.round == 1]
        assert len(r1_done) >= MIN_AGENTS_FOR_SYNTHESIS

    @pytest.mark.asyncio
    async def test_stream_agent_done_content_nonempty(self, mock_client) -> None:
        """AgentDoneEvent の content が非空文字列であること"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        agent_done_events = [e for e in events if isinstance(e, AgentDoneEvent)]
        for event in agent_done_events:
            assert isinstance(event.content, str)
            assert len(event.content) > 0

    @pytest.mark.asyncio
    async def test_stream_round_summary_diversity_in_range(self, mock_client) -> None:
        """RoundSummaryEvent の diversity_score_phi が [0.0, 1.0] 範囲内であること"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        summary_events = [e for e in events if isinstance(e, RoundSummaryEvent)]
        for event in summary_events:
            assert 0.0 <= event.diversity_score_phi <= 1.0

    @pytest.mark.asyncio
    async def test_stream_pre_mortem_failure_scenarios_nonempty(self, mock_client) -> None:
        """PreMortemDoneEvent の failure_scenarios が非空リストであること"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="standard", client=mock_client
        )]
        pm_events = [e for e in events if isinstance(e, PreMortemDoneEvent)]
        assert len(pm_events) == 1
        assert isinstance(pm_events[0].failure_scenarios, list)
        assert len(pm_events[0].failure_scenarios) > 0

    # ── バリデーションテスト ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_stream_empty_question_raises_value_error(self, mock_client) -> None:
        """空の question は ValueError を送出すること"""
        with pytest.raises(ValueError, match="空"):
            async for _ in stream_debate("", client=mock_client):
                pass

    @pytest.mark.asyncio
    async def test_stream_whitespace_question_raises_value_error(self, mock_client) -> None:
        """空白のみの question は ValueError を送出すること"""
        with pytest.raises(ValueError, match="空"):
            async for _ in stream_debate("  \t\n  ", client=mock_client):
                pass

    @pytest.mark.asyncio
    async def test_stream_invalid_mode_raises_value_error(self, mock_client) -> None:
        """不正な mode は ValueError を送出すること"""
        with pytest.raises(ValueError, match="mode"):
            async for _ in stream_debate(
                "テスト経営課題", mode="invalid", client=mock_client  # type: ignore[arg-type]
            ):
                pass

    # ── エラー耐性テスト ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_stream_all_failing_raises_runtime_error(
        self, mock_client_all_failing
    ) -> None:
        """全エージェントが失敗した場合 RuntimeError を送出すること"""
        with pytest.raises(RuntimeError, match="最低要件"):
            async for _ in stream_debate("テスト経営課題", client=mock_client_all_failing):
                pass


# ────────────────────────────────────────────────────────────────────
# Phase 3.5 テスト（トークンストリーミング・コスト追跡）
# ────────────────────────────────────────────────────────────────────

class TestPricing:
    """engine/pricing.py の単体テスト（Opus 設計諮問 Q4）"""

    def test_compute_cost_known_debate_model(self) -> None:
        """DEBATE_MODEL の既知価格でコストが正の数になること"""
        from engine.agents import DEBATE_MODEL
        cost = compute_cost(DEBATE_MODEL, input_tokens=1000, output_tokens=500)
        assert cost > 0

    def test_compute_cost_known_summary_model(self) -> None:
        """SUMMARY_MODEL の既知価格でコストが正の数になること"""
        from engine.agents import SUMMARY_MODEL
        cost = compute_cost(SUMMARY_MODEL, input_tokens=1000, output_tokens=500)
        assert cost > 0

    def test_compute_cost_unknown_model_returns_zero(self) -> None:
        """未知モデルは Decimal('0') を返すこと"""
        from decimal import Decimal
        cost = compute_cost("unknown-model-xyz", input_tokens=100, output_tokens=50)
        assert cost == Decimal("0")

    def test_compute_cost_zero_tokens(self) -> None:
        """トークン数0の場合はコスト0になること"""
        from decimal import Decimal
        from engine.agents import DEBATE_MODEL
        cost = compute_cost(DEBATE_MODEL, input_tokens=0, output_tokens=0)
        assert cost == Decimal("0")

    def test_compute_cost_debate_model_more_expensive(self) -> None:
        """DEBATE_MODEL（Opus）は SUMMARY_MODEL（Haiku）より高価なこと"""
        from engine.agents import DEBATE_MODEL, SUMMARY_MODEL
        cost_opus = compute_cost(DEBATE_MODEL, input_tokens=100, output_tokens=50)
        cost_haiku = compute_cost(SUMMARY_MODEL, input_tokens=100, output_tokens=50)
        assert cost_opus > cost_haiku

    def test_total_cost_empty_returns_zero(self) -> None:
        """空リストの合計コストは0"""
        from decimal import Decimal
        assert total_cost([]) == Decimal("0")

    def test_total_cost_single_call(self) -> None:
        """単一呼び出しの total_cost は compute_cost と一致すること"""
        from engine.agents import DEBATE_MODEL
        single = compute_cost(DEBATE_MODEL, 100, 50)
        agg = total_cost([(DEBATE_MODEL, 100, 50)])
        assert single == agg

    def test_total_cost_multiple_calls(self) -> None:
        """複数呼び出しの total_cost は各コストの和であること"""
        from decimal import Decimal
        from engine.agents import DEBATE_MODEL, SUMMARY_MODEL
        expected = (
            compute_cost(DEBATE_MODEL, 100, 50)
            + compute_cost(SUMMARY_MODEL, 200, 80)
        )
        result = total_cost([(DEBATE_MODEL, 100, 50), (SUMMARY_MODEL, 200, 80)])
        assert result == expected

    def test_model_prices_has_debate_model(self) -> None:
        """MODEL_PRICES に DEBATE_MODEL が含まれること"""
        from engine.agents import DEBATE_MODEL
        assert DEBATE_MODEL in MODEL_PRICES

    def test_model_prices_has_summary_model(self) -> None:
        """MODEL_PRICES に SUMMARY_MODEL が含まれること"""
        from engine.agents import SUMMARY_MODEL
        assert SUMMARY_MODEL in MODEL_PRICES

    def test_cost_precision_four_decimal_places(self) -> None:
        """compute_cost の結果は小数点以下4桁に丸められること"""
        from decimal import Decimal
        from engine.agents import DEBATE_MODEL
        cost = compute_cost(DEBATE_MODEL, 1, 1)
        # 小数点以下桁数が4以下であること
        assert cost == cost.quantize(Decimal("0.0001"))


class TestStreamDebatePhase35:
    """
    Phase 3.5：トークン単位ストリーミングイベントのテスト。
    standard / deep モードで AgentContentDeltaEvent・SynthesisDeltaEvent が yield されること。
    speed モードではこれらのイベントが emit されないこと。
    """

    # ── AgentContentDeltaEvent ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_standard_mode_emits_agent_content_delta(self, mock_client) -> None:
        """standard モードでは AgentContentDeltaEvent が yield されること（F-010）"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="standard", client=mock_client
        )]
        delta_events = [e for e in events if isinstance(e, AgentContentDeltaEvent)]
        assert len(delta_events) > 0

    @pytest.mark.asyncio
    async def test_deep_mode_emits_agent_content_delta(self, mock_client) -> None:
        """deep モードでも AgentContentDeltaEvent が yield されること（F-010）"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="deep", client=mock_client
        )]
        delta_events = [e for e in events if isinstance(e, AgentContentDeltaEvent)]
        assert len(delta_events) > 0

    @pytest.mark.asyncio
    async def test_speed_mode_no_agent_content_delta(self, mock_client) -> None:
        """speed モードでは AgentContentDeltaEvent が yield されないこと（F-010 対象外）"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="speed", client=mock_client
        )]
        delta_events = [e for e in events if isinstance(e, AgentContentDeltaEvent)]
        assert len(delta_events) == 0

    @pytest.mark.asyncio
    async def test_agent_content_delta_has_round_and_agent_id(self, mock_client) -> None:
        """AgentContentDeltaEvent に round・agent_id が設定されていること"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="standard", client=mock_client
        )]
        delta_events = [e for e in events if isinstance(e, AgentContentDeltaEvent)]
        for evt in delta_events:
            assert isinstance(evt.round, int)
            assert evt.round in (1, 2)
            assert isinstance(evt.agent_id, str)
            assert len(evt.agent_id) > 0

    @pytest.mark.asyncio
    async def test_agent_content_delta_text_chunk_is_str(self, mock_client) -> None:
        """AgentContentDeltaEvent の text_chunk が文字列であること"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="standard", client=mock_client
        )]
        delta_events = [e for e in events if isinstance(e, AgentContentDeltaEvent)]
        for evt in delta_events:
            assert isinstance(evt.text_chunk, str)

    # ── SynthesisDeltaEvent ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_standard_mode_emits_synthesis_delta(self, mock_client) -> None:
        """standard モードでは SynthesisDeltaEvent が yield されること（F-010）"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="standard", client=mock_client
        )]
        sdelta = [e for e in events if isinstance(e, SynthesisDeltaEvent)]
        assert len(sdelta) > 0

    @pytest.mark.asyncio
    async def test_speed_mode_no_synthesis_delta(self, mock_client) -> None:
        """speed モードでは SynthesisDeltaEvent が yield されないこと（F-010 対象外）"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="speed", client=mock_client
        )]
        sdelta = [e for e in events if isinstance(e, SynthesisDeltaEvent)]
        assert len(sdelta) == 0

    @pytest.mark.asyncio
    async def test_synthesis_delta_precedes_synthesis_done(self, mock_client) -> None:
        """SynthesisDeltaEvent は SynthesisDoneEvent より前に出現すること"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="standard", client=mock_client
        )]
        types = [type(e).__name__ for e in events]
        # SynthesisDeltaEvent が存在し、SynthesisDoneEvent より前に来ること
        if "SynthesisDeltaEvent" in types:
            first_delta_idx = types.index("SynthesisDeltaEvent")
            done_idx = types.index("SynthesisDoneEvent")
            assert first_delta_idx < done_idx

    # ── CompleteEvent.total_cost_usd ───────────────────────────────

    @pytest.mark.asyncio
    async def test_complete_event_has_total_cost_usd(self, mock_client) -> None:
        """CompleteEvent に total_cost_usd フィールドが存在すること（Phase 3.5）"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        complete = next(e for e in events if isinstance(e, CompleteEvent))
        assert hasattr(complete, "total_cost_usd")
        assert isinstance(complete.total_cost_usd, float)

    @pytest.mark.asyncio
    async def test_complete_event_total_cost_nonnegative(self, mock_client) -> None:
        """CompleteEvent.total_cost_usd が非負であること"""
        events = [e async for e in stream_debate("テスト経営課題", client=mock_client)]
        complete = next(e for e in events if isinstance(e, CompleteEvent))
        assert complete.total_cost_usd >= 0.0

    @pytest.mark.asyncio
    async def test_standard_mode_total_cost_positive(self, mock_client) -> None:
        """standard モードでは total_cost_usd > 0（mock usage=100/50 tokens）"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="standard", client=mock_client
        )]
        complete = next(e for e in events if isinstance(e, CompleteEvent))
        assert complete.total_cost_usd > 0.0

    @pytest.mark.asyncio
    async def test_speed_mode_total_cost_nonnegative(self, mock_client) -> None:
        """speed モードでも total_cost_usd >= 0（summary/synthesis は追跡対象）"""
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="speed", client=mock_client
        )]
        complete = next(e for e in events if isinstance(e, CompleteEvent))
        assert complete.total_cost_usd >= 0.0

    # ── AgentErrorEvent（フィールド確認）─────────────────────────

    def test_agent_error_event_fields(self) -> None:
        """AgentErrorEvent のフィールドが正しく設定されること"""
        evt = AgentErrorEvent(round=1, agent_id="strategist", error="タイムアウト")
        assert evt.round == 1
        assert evt.agent_id == "strategist"
        assert evt.error == "タイムアウト"
        assert evt.event_type == "agent_error"

    @pytest.mark.asyncio
    async def test_generator_early_exit_does_not_raise(self, mock_client) -> None:
        """generator を途中で break しても例外を送出しないこと（Opus指摘①：task leak 修正の回帰テスト）"""
        from engine.debate import SessionStartEvent

        gen = stream_debate("テスト経営課題", mode="standard", client=mock_client)
        # 最初の 1 イベントだけ受信して打ち切る
        first_event = None
        async for evt in gen:
            first_event = evt
            break  # 早期終了
        await gen.aclose()  # 明示的にクリーンアップ

        assert first_event is not None
        assert isinstance(first_event, SessionStartEvent)

    @pytest.mark.asyncio
    async def test_complete_event_total_cost_type_is_float(self, mock_client) -> None:
        """CompleteEvent.total_cost_usd が float 型であること（JSON シリアライズ可能性の確認）"""
        import json as _json
        events = [e async for e in stream_debate(
            "テスト経営課題", mode="standard", client=mock_client
        )]
        complete = next(e for e in events if isinstance(e, CompleteEvent))
        assert isinstance(complete.total_cost_usd, float)
        # JSON シリアライズ可能であること（SSE 配信に必要）
        serialized = _json.dumps({"total_cost_usd": complete.total_cost_usd})
        assert "total_cost_usd" in serialized


# ───────────────────────────────────────
# 匿名査読テスト（仕様書 §4.2）
# ───────────────────────────────────────

class TestAnonymizeSummary:
    """_anonymize_summary() および _ANON_LABELS の不変条件を検証する（仕様書 §4.2）"""

    def test_anon_labels_covers_all_five_agents(self) -> None:
        """_ANON_LABELS が全5エージェントIDを網羅していること"""
        expected_agents = {"strategist", "cfo", "engineer", "market", "risk"}
        assert set(_ANON_LABELS.keys()) == expected_agents

    def test_anon_labels_unique_values(self) -> None:
        """匿名ラベルが重複なく割り当てられていること"""
        labels = list(_ANON_LABELS.values())
        assert len(labels) == len(set(labels))

    def test_anonymize_replaces_agent_id_headings(self) -> None:
        """## strategist 等の見出しが匿名ラベルに置換されること"""
        text = "## strategist\n戦略的観点から...\n## cfo\n財務的観点から..."
        result = _anonymize_summary(text)
        assert "## strategist" not in result
        assert "## cfo" not in result
        assert "## 意見A" in result
        assert "## 意見B" in result

    def test_anonymize_does_not_replace_inline_agent_id(self) -> None:
        """見出し（## ）以外のエージェントID文言は置換されないこと"""
        text = "strategistが主張した通り...\n## strategist\n詳細はこちら"
        result = _anonymize_summary(text)
        # インライン文言は残る
        assert "strategistが主張した通り" in result
        # 見出しは匿名化される
        assert "## strategist" not in result
        assert "## 意見A" in result

    def test_anonymize_empty_string_returns_empty(self) -> None:
        """空文字列を渡しても空文字列が返ること"""
        assert _anonymize_summary("") == ""

    def test_anonymize_no_agent_headings_unchanged(self) -> None:
        """エージェント見出しが含まれないテキストは変更されないこと"""
        text = "Round 1 の総合要約です。全エージェントが同意しています。"
        assert _anonymize_summary(text) == text

    def test_anonymize_all_five_agents(self) -> None:
        """全5エージェント見出しが対応する意見ラベルに置換されること"""
        lines = [f"## {aid}" for aid in _ANON_LABELS]
        text = "\n".join(lines)
        result = _anonymize_summary(text)
        for agent_id in _ANON_LABELS:
            assert f"## {agent_id}" not in result
        for label in _ANON_LABELS.values():
            assert f"## {label}" in result
