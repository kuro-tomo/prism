"""
engine/pricing.py — Anthropic モデル価格計算（Phase 3.5）

設計書 §3 models + Opus 設計諮問 Q4 に基づく。
モデルバージョン直紐付けで S-005（ピン留め）と整合。
価格改定時はこのファイルのみ更新すれば良い。
"""
from __future__ import annotations

from decimal import Decimal

# Anthropic 公式価格（2026-05 時点・per 1M tokens・USD）
# S-005：モデルバージョンを日付付きでピン留め。価格改定時はここのみ更新。
# (input_per_million, output_per_million)
MODEL_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    # engine/agents.py DEBATE_MODEL
    "claude-opus-4-8":             (Decimal("15.00"), Decimal("75.00")),
    # engine/agents.py SUMMARY_MODEL
    "claude-haiku-4-5-20251001":   (Decimal("0.80"),  Decimal("4.00")),
}


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """
    指定モデル・トークン数に対する API コスト（USD）を計算する。

    Args:
        model:         Anthropic モデル識別子（例: "claude-opus-4-5-20251104"）
        input_tokens:  入力トークン数
        output_tokens: 出力トークン数

    Returns:
        Decimal: コスト（USD, 小数点以下4桁まで）。
        未知モデルは Decimal("0") を返す（ログは呼び出し元が必要に応じて追加）。
    """
    prices = MODEL_PRICES.get(model)
    if prices is None:
        return Decimal("0")
    in_p, out_p = prices
    cost = (Decimal(input_tokens) * in_p + Decimal(output_tokens) * out_p) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.0001"))


def total_cost(calls: list[tuple[str, int, int]]) -> Decimal:
    """
    複数 API 呼び出しのコストを合算する。

    Args:
        calls: [(model, input_tokens, output_tokens), ...] のリスト

    Returns:
        Decimal: 合計コスト（USD, 小数点以下4桁まで）
    """
    return sum(
        (compute_cost(m, i, o) for m, i, o in calls),
        start=Decimal("0"),
    ).quantize(Decimal("0.0001"))
