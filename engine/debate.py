"""
3ラウンド熟議エンジン

フロー:
  Round 1 — 各エージェントが独立回答（相互参照なし）
  Round 2 — 全意見を共有した上で反論・深掘り
  Round 3 — 統合者が「第三の解」を起草

TODO（/advisor 諮問後に実装）:
  - run_debate() の本実装
  - 収束チェック（早期終了）ロジック
  - 並列API呼び出しによる高速化
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class DebateResult:
    question: str
    memory_context: str
    round1: dict[str, str] = field(default_factory=dict)
    round2: dict[str, str] = field(default_factory=dict)
    synthesis: str = ""


def run_debate(question: str, memory_context: str = "") -> DebateResult:
    """
    擬似フィーラの中核関数。
    実装は設計書確定後に行う。
    """
    raise NotImplementedError("設計諮問（/advisor）後に実装")
