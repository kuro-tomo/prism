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
