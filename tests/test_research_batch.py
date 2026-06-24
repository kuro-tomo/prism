"""
tests/test_research_batch.py — scripts/research_batch.py のユニットテスト（F-022）
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.research_batch import _letter_to_category, parse_scenarios

SCENARIO_MD = Path(__file__).parent.parent / "docs" / "標準化シナリオ集.md"


class TestLetterToCategory:
    def test_a_maps_to_strategy(self) -> None:
        assert _letter_to_category("A") == "strategy"

    def test_b_maps_to_financial(self) -> None:
        assert _letter_to_category("B") == "financial"

    def test_unknown_maps_to_other(self) -> None:
        assert _letter_to_category("Z") == "other"

    def test_lowercase_accepted(self) -> None:
        assert _letter_to_category("a") == "strategy"


class TestParseScenarios:
    def test_count_is_50(self) -> None:
        scenarios = parse_scenarios(SCENARIO_MD)
        assert len(scenarios) == 50, f"期待50件、実際{len(scenarios)}件"

    def test_required_keys_present(self) -> None:
        scenarios = parse_scenarios(SCENARIO_MD)
        for sc in scenarios:
            assert "title" in sc and sc["title"]
            assert "background" in sc and sc["background"]
            assert "question" in sc and sc["question"]
            assert "category" in sc

    def test_categories_are_valid(self) -> None:
        valid = {"strategy", "financial", "org", "market", "risk", "hr", "digital", "other"}
        scenarios = parse_scenarios(SCENARIO_MD)
        for sc in scenarios:
            assert sc["category"] in valid, f"不正カテゴリ: {sc['category']}"

    def test_first_scenario_title(self) -> None:
        scenarios = parse_scenarios(SCENARIO_MD)
        assert scenarios[0]["title"] == "主力事業の縮小と新規参入"

    def test_background_not_empty(self) -> None:
        scenarios = parse_scenarios(SCENARIO_MD)
        for sc in scenarios:
            assert len(sc["background"]) > 20, f"背景が短すぎる: {sc['title']}"
