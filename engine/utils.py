"""
engine/utils.py — 熟議品質計測ユーティリティ

責務:
  diversity_score     意見の多様性を計算（CFIM 干渉ポテンシャル Φ の近似）
  convergence_check   Round 2 収束（全員一致リスク）を判定
  estimate_cost       API 呼び出しコスト見積もり

設計書 §9.1（多様性チェック）・§9.2（収束チェック）準拠。
外部依存なし — 標準ライブラリのみ使用（numpy・sklearn 不要）。
"""

from __future__ import annotations

import math
import re
from typing import Final

# ── トークン価格（USD/token）──────────────────────────────────────────
# 出典: Anthropic pricing page（2026-05 時点）
# 価格改訂時はここのみを更新すること
_PRICING: Final[dict[str, dict[str, float]]] = {
    "claude-opus-4-20250514": {
        "input":  15.0 / 1_000_000,   # $15 per 1M input tokens
        "output": 75.0 / 1_000_000,   # $75 per 1M output tokens
    },
    "claude-haiku-4-5-20251001": {
        "input":  0.80 / 1_000_000,   # $0.80 per 1M input tokens
        "output": 4.00 / 1_000_000,   # $4.00 per 1M output tokens
    },
}
_DEFAULT_PRICING: Final[dict[str, float]] = _PRICING["claude-opus-4-20250514"]

# 多様性スコアの警告閾値（仕様書 §9.1）
DIVERSITY_WARN_THRESHOLD: Final[float] = 0.3
# 収束チェック閾値: Round 2 多様性がこれ未満なら全員一致リスクとみなす
CONVERGENCE_THRESHOLD: Final[float] = 0.15


# ── 内部ヘルパー ─────────────────────────────────────────────────────

def _tokenize(text: str) -> dict[str, int]:
    """Bag-of-Words: 英数字・日本語文字を抽出してカウント"""
    words = re.findall(r"[\w぀-鿿ｦ-ﾟ]+", text.lower())
    counts: dict[str, int] = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    return counts


def _cosine_similarity(a: dict[str, int], b: dict[str, int]) -> float:
    """2つの単語頻度ベクトルのコサイン類似度 ∈ [0, 1]"""
    dot = sum(a.get(k, 0) * v for k, v in b.items())
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── 公開 API ─────────────────────────────────────────────────────────

def diversity_score(texts: list[str]) -> float:
    """
    意見の多様性スコアを計算する。

    多様性スコア = 1.0 − 全ペアのコサイン類似度の平均 ∈ [0, 1]
    （完全一致なら 0.0、完全無関係なら 1.0）

    設計書 §9.1 / 仕様書 §9.1 「CFIM 干渉ポテンシャル Φ の測定」準拠。
    スコア < DIVERSITY_WARN_THRESHOLD の場合はログ警告すること（呼び出し元の責務）。

    Args:
        texts: エージェント発言テキストのリスト（2件以上推奨）

    Returns:
        多様性スコア float ∈ [0, 1]（0〜1件の場合は 1.0 を返す）
    """
    if len(texts) < 2:
        return 1.0

    vectors = [_tokenize(t) for t in texts]
    pairs = [
        (i, j)
        for i in range(len(vectors))
        for j in range(i + 1, len(vectors))
    ]
    if not pairs:
        return 1.0

    avg_sim = (
        sum(_cosine_similarity(vectors[i], vectors[j]) for i, j in pairs)
        / len(pairs)
    )
    return max(0.0, 1.0 - avg_sim)


def convergence_check(texts: list[str]) -> bool:
    """
    Round 2 収束チェック（設計書 §9.2）。

    True を返す = 全員一致リスクあり（合意リスクシグナル）。
    Round 3 統合プロンプトに「構造的原因を分析せよ」を追加する目印として使う。

    Args:
        texts: Round 2 エージェント発言テキストのリスト

    Returns:
        True  = 収束（diversity_score < CONVERGENCE_THRESHOLD）
        False = 多様性あり（正常）
    """
    score = diversity_score(texts)
    return score < CONVERGENCE_THRESHOLD


def compute_novelty_flag(phi_r1: float, phi_r2: float) -> bool:
    """
    新規性フラグを算出する（CFIM §9.2 Definition 9.1）。

    Round 2 で多様性が拡大（phi_r2 > phi_r1）した場合に True を返す。
    Round 2 はエージェントが互いの意見を読んで反論するラウンドであり、
    収束が自然な流れである。そこで多様性が増加した場合は、
    新たな視点・論点が創発された証拠とみなす（認知フレーム干渉が発生）。

    Args:
        phi_r1: Round 1 多様性スコア Φ_R1 ∈ [0, 1]
        phi_r2: Round 2 多様性スコア Φ_R2 ∈ [0, 1]

    Returns:
        True  = 新規性あり（phi_r2 > phi_r1）
        False = 収束・同水準
    """
    return phi_r2 > phi_r1


def compute_fes(
    phi_r2: float,
    g_orig: int,
    novelty_flag: bool,
    *,
    alpha: float = 0.4,
    beta: float = 0.4,
    gamma: float = 0.2,
) -> float:
    """
    FES（Frame Emergence Score）を算出する（CFIM §9.2 Definition 9.1）。

    FES = α·Φ_R2 + β·(G_orig − 1) / 4 + γ·1[novelty]

    係数の意味:
        α = 0.4 — Round 2 干渉ポテンシャルの重み（多様性の直接寄与）
        β = 0.4 — 独自性評点の重み（人間評価の創発度合い）
        γ = 0.2 — 新規性フラグの重み（Round 間の創発ボーナス）

    制約: α + β + γ = 1.0 を呼び出し元で確認すること。

    Args:
        phi_r2:       Round 2 多様性スコア Φ_R2 ∈ [0, 1]
        g_orig:       独自性評点 G_orig ∈ [1, 5]（人間評価者が付与）
        novelty_flag: compute_novelty_flag() の戻り値
        alpha:        Φ_R2 係数（既定 0.4）
        beta:         G_orig 係数（既定 0.4）
        gamma:        novelty_flag 係数（既定 0.2）

    Returns:
        FES ∈ [0, 1]（小数点以下4桁に丸め）

    Raises:
        ValueError: phi_r2 / g_orig が None または NaN の場合。
                    クランプ式 max(0,min(1,raw)) は NaN を黙って 1.0 に化けさせ
                    （min/max の順序依存）、eval_runs に偽の満点 FES=1.0 を
                    記録して回帰分析を汚染する恐れがあるため、入口で弾く。
    """
    if phi_r2 is None or g_orig is None:
        raise ValueError(f"compute_fes: phi_r2/g_orig に None は不可（phi_r2={phi_r2}, g_orig={g_orig}）")
    if math.isnan(float(phi_r2)) or math.isnan(float(g_orig)):
        raise ValueError(f"compute_fes: phi_r2/g_orig に NaN は不可（phi_r2={phi_r2}, g_orig={g_orig}）")
    g_norm = (g_orig - 1) / 4.0   # [1,5] → [0,1] に正規化
    novelty = 1.0 if novelty_flag else 0.0
    raw = alpha * phi_r2 + beta * g_norm + gamma * novelty
    return round(max(0.0, min(1.0, raw)), 4)


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> float:
    """
    API 呼び出しコストを見積もる（USD）。

    Args:
        input_tokens:  入力トークン数
        output_tokens: 出力トークン数
        model:         モデル名（日付付きピン留め済みバージョン文字列）

    Returns:
        推定コスト（USD）。不明なモデルは Opus 価格で保守的に見積もる。
    """
    pricing = _PRICING.get(model, _DEFAULT_PRICING)
    return input_tokens * pricing["input"] + output_tokens * pricing["output"]
