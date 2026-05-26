"""
エージェント定義モジュール
各専門エージェントのペルソナ（システムプロンプト）を管理する

TODO（/advisor 諮問後に実装）:
  - エージェント一覧の確定
  - 各ペルソナの品質チューニング
  - テクノ中部業種への特化度の調整
"""

from dataclasses import dataclass


@dataclass
class Agent:
    name: str
    persona: str
    model: str = "claude-opus-4-5"   # 討論役はOpus
    max_tokens: int = 800


# ── エージェント定義（スタブ：設計諮問後に確定） ──────────────────
AGENTS: dict[str, Agent] = {
    # 実装は /advisor 諮問・設計書確定後に追記
}

SYNTHESIZER_NAME = "統合者"
