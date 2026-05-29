"""
tests/test_agents.py — engine/agents.py 構造テスト

方針（Opus 審査 2026-05-27 指摘事項）:
  - プロンプト本文の完全一致テストは禁忌（将来の改善反復を阻害するため）
  - 構造・型・範囲・キー整合のみを検証する
"""

import pytest
from dataclasses import fields

from engine.agents import (
    AGENTS,
    DEFAULT_ROSTER,
    DEBATE_MODEL,
    SUMMARY_MODEL,
    MAX_TOKENS_DEBATE,
    MAX_TOKENS_SUMMARY,
    AgentSpec,
    get_roster,
)


class TestAgentSpec:
    """AgentSpec データクラスの構造テスト"""

    def test_frozen(self) -> None:
        """AgentSpec は frozen=True でイミュータブルであること（直接代入が FrozenInstanceError を送出）"""
        agent = AGENTS["strategist"]
        with pytest.raises(AttributeError):  # FrozenInstanceError は AttributeError のサブクラス
            agent.temperature = 0.9  # type: ignore[misc]

    def test_required_fields(self) -> None:
        """AgentSpec が 4 フィールドを持つこと"""
        field_names = {f.name for f in fields(AgentSpec)}
        assert field_names == {"id", "name", "temperature", "system_prompt"}

    def test_temperature_range(self) -> None:
        """全エージェントの temperature が [0.0, 1.0] 内にあること"""
        for agent_id, agent in AGENTS.items():
            assert 0.0 <= agent.temperature <= 1.0, (
                f"{agent_id}: temperature={agent.temperature} が範囲外"
            )

    def test_system_prompt_nonempty(self) -> None:
        """全エージェントの system_prompt が空でないこと"""
        for agent_id, agent in AGENTS.items():
            assert agent.system_prompt.strip(), f"{agent_id}: system_prompt が空"

    def test_name_nonempty(self) -> None:
        """全エージェントの name が空でないこと"""
        for agent_id, agent in AGENTS.items():
            assert agent.name.strip(), f"{agent_id}: name が空"

    def test_id_nonempty(self) -> None:
        """全エージェントの id が空でないこと"""
        for agent_id, agent in AGENTS.items():
            assert agent.id.strip(), f"{agent_id}: id が空"


class TestAgentsDict:
    """AGENTS dict の整合性テスト"""

    def test_key_matches_id(self) -> None:
        """AGENTS dict のキーと AgentSpec.id が一致すること"""
        for key, agent in AGENTS.items():
            assert key == agent.id, (
                f"キー '{key}' と AgentSpec.id '{agent.id}' が不一致"
            )

    def test_no_duplicate_ids(self) -> None:
        """AGENTS 内に重複 ID がないこと"""
        ids = [a.id for a in AGENTS.values()]
        assert len(ids) == len(set(ids)), "AGENTS 内に重複 ID が存在する"

    def test_five_agents(self) -> None:
        """AGENTS が 5 体（philosopher 除く）であること"""
        assert len(AGENTS) == 5

    def test_required_agent_ids_present(self) -> None:
        """必須エージェント 5 体が全て存在すること"""
        required = {"strategist", "cfo", "engineer", "market", "risk"}
        assert required.issubset(AGENTS.keys()), (
            f"不足している ID: {required - AGENTS.keys()}"
        )

    def test_philosopher_not_in_agents(self) -> None:
        """philosopher は Phase 1 未実装のため AGENTS に含まれないこと"""
        assert "philosopher" not in AGENTS


class TestDefaultRoster:
    """DEFAULT_ROSTER の整合性テスト"""

    def test_roster_length(self) -> None:
        """DEFAULT_ROSTER が 5 体であること"""
        assert len(DEFAULT_ROSTER) == 5

    def test_roster_ids_exist_in_agents(self) -> None:
        """DEFAULT_ROSTER の全 ID が AGENTS に存在すること"""
        for aid in DEFAULT_ROSTER:
            assert aid in AGENTS, f"DEFAULT_ROSTER の '{aid}' が AGENTS に存在しない"

    def test_roster_no_duplicates(self) -> None:
        """DEFAULT_ROSTER に重複がないこと"""
        assert len(DEFAULT_ROSTER) == len(set(DEFAULT_ROSTER))

    def test_roster_is_tuple(self) -> None:
        """DEFAULT_ROSTER がタプルであること（ミュータブルな list でないこと）"""
        assert isinstance(DEFAULT_ROSTER, tuple)


class TestGetRoster:
    """get_roster() 関数のテスト"""

    def test_default_returns_five(self) -> None:
        """デフォルト呼び出しで 5 体の名簿を返すこと"""
        roster = get_roster()
        assert len(roster) == 5

    def test_returns_tuple(self) -> None:
        """タプルを返すこと"""
        assert isinstance(get_roster(), tuple)

    def test_matches_default_roster(self) -> None:
        """get_roster() の結果が DEFAULT_ROSTER と一致すること"""
        assert get_roster() == DEFAULT_ROSTER

    def test_philosopher_raises(self) -> None:
        """include_philosopher=True で NotImplementedError が発生すること"""
        with pytest.raises(NotImplementedError):
            get_roster(include_philosopher=True)


class TestModelConstants:
    """モデルバージョン・トークン上限の構造テスト"""

    def test_debate_model_pinned(self) -> None:
        """DEBATE_MODEL が日付付きバージョンで固定されていること（-latest 禁止）"""
        assert "latest" not in DEBATE_MODEL.lower()
        assert DEBATE_MODEL  # 空でないこと

    def test_summary_model_pinned(self) -> None:
        """SUMMARY_MODEL が日付付きバージョンで固定されていること（-latest 禁止）"""
        assert "latest" not in SUMMARY_MODEL.lower()
        assert SUMMARY_MODEL  # 空でないこと

    def test_max_tokens_positive(self) -> None:
        """MAX_TOKENS_DEBATE・MAX_TOKENS_SUMMARY が正の整数であること"""
        assert MAX_TOKENS_DEBATE > 0
        assert MAX_TOKENS_SUMMARY > 0

    def test_models_differ(self) -> None:
        """討論モデルと要約モデルが異なること（両方 Opus は設計違反）"""
        assert DEBATE_MODEL != SUMMARY_MODEL


class TestTemperatureDiversity:
    """多様性確保のための温度設計テスト（設計書 §4.2）"""

    def test_temperatures_are_diverse(self) -> None:
        """5体の temperature に少なくとも 3 種類以上の異なる値があること"""
        temps = {a.temperature for a in AGENTS.values()}
        assert len(temps) >= 3, (
            f"温度の種類が少なすぎる（{len(temps)} 種類）。多様性確保のため 3 種類以上が必要。"
        )

    def test_risk_agent_most_conservative(self) -> None:
        """risk エージェントが最も低い temperature を持つこと（設計書 §4.2 の意図）"""
        risk_temp = AGENTS["risk"].temperature
        for agent_id, agent in AGENTS.items():
            if agent_id != "risk":
                assert risk_temp <= agent.temperature, (
                    f"risk ({risk_temp}) より {agent_id} ({agent.temperature}) の方が低い"
                )

    def test_market_agent_most_creative(self) -> None:
        """market エージェントが最も高い temperature を持つこと（設計書 §4.2 の意図）"""
        market_temp = AGENTS["market"].temperature
        for agent_id, agent in AGENTS.items():
            if agent_id != "market":
                assert market_temp >= agent.temperature, (
                    f"market ({market_temp}) より {agent_id} ({agent.temperature}) の方が高い"
                )
