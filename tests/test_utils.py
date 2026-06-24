"""
tests/test_utils.py — engine/utils.py ユーティリティ関数のテスト

対象:
  diversity_score     — 設計書 §9.1
  convergence_check   — 設計書 §9.2
  compute_novelty_flag — CFIM §9.2 新規性フラグ（仕様書 F-022）
  compute_fes          — CFIM §9.2 FES 計算（仕様書 F-022）
"""
import math
import pytest

from engine.utils import (
    CONVERGENCE_THRESHOLD,
    DIVERSITY_WARN_THRESHOLD,
    compute_fes,
    compute_novelty_flag,
    convergence_check,
    diversity_score,
)


# ===========================
# §1 diversity_score（設計書 §9.1）
# ===========================

class TestDiversityScore:
    def test_empty_list_returns_one(self) -> None:
        assert diversity_score([]) == 1.0

    def test_single_text_returns_one(self) -> None:
        assert diversity_score(["only one"]) == 1.0

    def test_identical_texts_returns_zero(self) -> None:
        texts = ["コスト削減を優先すべきだ"] * 3
        score = diversity_score(texts)
        assert score == pytest.approx(0.0, abs=1e-6)

    def test_completely_different_texts_returns_one(self) -> None:
        texts = ["りんご みかん", "xyz abc def"]
        score = diversity_score(texts)
        assert score == pytest.approx(1.0, abs=1e-6)

    def test_score_in_range(self) -> None:
        texts = ["設備投資を増やすべきだ", "人員削減を優先すべき", "新市場に進出すべき"]
        score = diversity_score(texts)
        assert 0.0 <= score <= 1.0

    def test_warn_threshold_is_float(self) -> None:
        assert isinstance(DIVERSITY_WARN_THRESHOLD, float)


# ===========================
# §2 convergence_check（設計書 §9.2）
# ===========================

class TestConvergenceCheck:
    def test_identical_texts_converge(self) -> None:
        texts = ["全員一致の意見です"] * 4
        assert convergence_check(texts) is True

    def test_diverse_texts_not_converge(self) -> None:
        texts = [
            "コスト削減を最優先にすべきだ",
            "新規事業への投資こそが活路だ",
            "人材育成に集中すべき局面だ",
        ]
        assert convergence_check(texts) is False

    def test_threshold_constant(self) -> None:
        assert CONVERGENCE_THRESHOLD == 0.15


# ===========================
# §3 compute_novelty_flag（CFIM §9.2 Definition 9.1）
# ===========================

class TestComputeNoveltyFlag:
    def test_phi_r2_greater_returns_true(self) -> None:
        """Round 2 で多様性が拡大 → 新規性あり。"""
        assert compute_novelty_flag(phi_r1=0.30, phi_r2=0.45) is True

    def test_phi_r2_equal_returns_false(self) -> None:
        """同水準 → 新規性なし。"""
        assert compute_novelty_flag(phi_r1=0.40, phi_r2=0.40) is False

    def test_phi_r2_less_returns_false(self) -> None:
        """Round 2 で収束 → 新規性なし（通常の収束パターン）。"""
        assert compute_novelty_flag(phi_r1=0.50, phi_r2=0.30) is False

    def test_both_zero_returns_false(self) -> None:
        assert compute_novelty_flag(phi_r1=0.0, phi_r2=0.0) is False

    def test_r1_zero_r2_positive_returns_true(self) -> None:
        assert compute_novelty_flag(phi_r1=0.0, phi_r2=0.01) is True


# ===========================
# §4 compute_fes（CFIM §9.2 Definition 9.1）
# ===========================

class TestComputeFes:
    def test_maximum_score(self) -> None:
        """全要素最大値 → FES ≈ 1.0。"""
        fes = compute_fes(phi_r2=1.0, g_orig=5, novelty_flag=True)
        assert fes == pytest.approx(1.0, abs=1e-4)

    def test_minimum_score(self) -> None:
        """全要素最小値 → FES = 0.0。"""
        fes = compute_fes(phi_r2=0.0, g_orig=1, novelty_flag=False)
        assert fes == pytest.approx(0.0, abs=1e-4)

    def test_formula_correctness(self) -> None:
        """FES = 0.4×Φ + 0.4×(G-1)/4 + 0.2×novelty の数値検証。"""
        # phi_r2=0.5, g_orig=3 → g_norm=0.5, novelty=0
        # FES = 0.4*0.5 + 0.4*0.5 + 0.2*0 = 0.2 + 0.2 = 0.4
        fes = compute_fes(phi_r2=0.5, g_orig=3, novelty_flag=False)
        assert fes == pytest.approx(0.4, abs=1e-4)

    def test_novelty_bonus(self) -> None:
        """novelty_flag=True は gamma(0.2) 分だけ FES を上乗せする。"""
        fes_without = compute_fes(phi_r2=0.5, g_orig=3, novelty_flag=False)
        fes_with    = compute_fes(phi_r2=0.5, g_orig=3, novelty_flag=True)
        assert fes_with == pytest.approx(fes_without + 0.2, abs=1e-4)

    def test_result_clamped_to_unit_interval(self) -> None:
        """FES ∈ [0, 1] に必ずクランプされる。"""
        # 係数合計が 1.0 超でも安全
        fes = compute_fes(phi_r2=1.0, g_orig=5, novelty_flag=True,
                          alpha=0.5, beta=0.5, gamma=0.5)
        assert 0.0 <= fes <= 1.0

    def test_custom_coefficients(self) -> None:
        """係数のカスタマイズが計算に反映される。"""
        fes = compute_fes(phi_r2=1.0, g_orig=1, novelty_flag=False,
                          alpha=1.0, beta=0.0, gamma=0.0)
        assert fes == pytest.approx(1.0, abs=1e-4)

    def test_g_orig_normalization(self) -> None:
        """g_orig=[1,2,3,4,5] が [0,0.25,0.5,0.75,1.0] に正規化される。"""
        expected_norms = [0.0, 0.25, 0.5, 0.75, 1.0]
        for g, expected_norm in zip(range(1, 6), expected_norms):
            fes = compute_fes(phi_r2=0.0, g_orig=g, novelty_flag=False,
                              alpha=0.0, beta=1.0, gamma=0.0)
            assert fes == pytest.approx(expected_norm, abs=1e-4), \
                f"g_orig={g} → expected {expected_norm}, got {fes}"

    def test_return_type_is_float(self) -> None:
        assert isinstance(compute_fes(0.5, 3, False), float)

    def test_rounded_to_4_decimals(self) -> None:
        """戻り値は小数点以下4桁に丸められる。"""
        fes = compute_fes(phi_r2=0.333, g_orig=2, novelty_flag=False)
        # 桁数確認: 小数点以下5桁目が存在しないこと
        str_val = str(fes)
        if "." in str_val:
            decimal_part = str_val.split(".")[1]
            assert len(decimal_part) <= 4

    def test_nan_phi_raises(self) -> None:
        """phi_r2=NaN は ValueError。クランプで偽 1.0 に化けるのを防ぐ。"""
        with pytest.raises(ValueError):
            compute_fes(phi_r2=float("nan"), g_orig=3, novelty_flag=False)

    def test_nan_g_orig_raises(self) -> None:
        """g_orig=NaN は ValueError。"""
        with pytest.raises(ValueError):
            compute_fes(phi_r2=0.5, g_orig=float("nan"), novelty_flag=False)  # type: ignore[arg-type]

    def test_none_phi_raises(self) -> None:
        """phi_r2=None は ValueError。"""
        with pytest.raises(ValueError):
            compute_fes(phi_r2=None, g_orig=3, novelty_flag=False)  # type: ignore[arg-type]

    def test_none_g_orig_raises(self) -> None:
        """g_orig=None は ValueError。"""
        with pytest.raises(ValueError):
            compute_fes(phi_r2=0.5, g_orig=None, novelty_flag=False)  # type: ignore[arg-type]

    def test_out_of_range_g_orig_clamped(self) -> None:
        """範囲外 g_orig（DB CHECK で本来弾かれる値）もクランプで [0,1] に収まる。"""
        # g_orig=6 → g_norm=1.25 だが beta=0.4 で 0.5、全体は [0,1] 内
        fes_high = compute_fes(phi_r2=1.0, g_orig=6, novelty_flag=True)
        assert 0.0 <= fes_high <= 1.0
        # g_orig=0 → g_norm=-0.25、クランプで下限 0.0 を割らない
        fes_low = compute_fes(phi_r2=0.0, g_orig=0, novelty_flag=False)
        assert 0.0 <= fes_low <= 1.0
