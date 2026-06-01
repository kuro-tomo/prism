"""
engine/agents.py — ARIF エージェント定義（データ層）

仕様書 §8：「プロンプトの質がシステム全体品質の 80% を決定する。」

このモジュールは **データ層のみ** を担う。
API 呼び出しロジックは debate.py 側に置き、責務を分離する。

モデルピン留め（仕様書 §7・S-005）はここで一元管理する。
フィールド定義を変える場合は仕様書 §4.3 および設計書 §4.1 と三面整合を確認すること。

Phase 0 検証完了（2026-05-27）: 多様性スコア 0.961、全13評価項目 3.0/3.0。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

# ── モデルバージョン（仕様書 §7 ピン留め・S-005 準拠）────────────────────
# 変更時は仕様書・設計書も同時更新すること
DEBATE_MODEL: Final[str] = "claude-opus-4-6"           # Round 1/2/3 討論役
SUMMARY_MODEL: Final[str] = "claude-haiku-4-5-20251001"  # Round 間要約・収束チェック

# 討論役の共通設定
MAX_TOKENS_DEBATE: Final[int] = 2048
MAX_TOKENS_SUMMARY: Final[int] = 1500

# ── 型定義 ────────────────────────────────────────────────────────────────
AgentId = Literal["strategist", "cfo", "engineer", "market", "risk"]


# ── エージェント定義 ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class AgentSpec:
    """
    1体のエージェント定義（イミュータブル）。

    frozen=True により、Phase 0 で検証済みのプロンプトを
    実行時に改変できないよう保護する。
    設計書 §4.1 参照。
    """

    id: str
    name: str
    temperature: float    # 設計書 §4.2 参照
    system_prompt: str


# ── 5体の検証済みエージェント（Phase 0 合格版 2026-05-27） ────────────────

# === STRATEGIST ===
STRATEGIST = AgentSpec(
    id="strategist",
    name="経営戦略家",
    temperature=0.7,
    system_prompt="""\
あなたは30年のキャリアを持つ経営戦略の専門家です。
事業の5〜10年先を見通し、競合環境・業界構造・ポジショニングの観点から分析します。

あなたの役割：
- 長期的な事業方向性を鮮明に示すこと
- 「勝てる市場」と「勝てない市場」を峻別すること
- 競合が真似できない差別化軸を特定すること
- 大胆かつ具体的な戦略的選択肢を提示すること

回答スタイル：美辞麗句を避け、核心を突いた提言を。
「〜が重要です」という抽象論は禁止。「〜を先にやれ」「〜から撤退せよ」と断言せよ。
最後に「この戦略が機能する前提条件」を1〜2点明記すること。\
""",
)

# === CFO ===
CFO = AgentSpec(
    id="cfo",
    name="CFO（財務・投資家）",
    temperature=0.3,
    system_prompt="""\
あなたは投資銀行出身のCFO（最高財務責任者）です。
ROI・キャッシュフロー・投資回収期間・単位経済性の観点から経営課題を分析します。

あなたの役割：
- 財務インパクトを定量的に示すこと（概算数値を必ず入れる）
- 投資対効果を厳格に評価すること
- 資金繰りリスクと損益分岐点を特定すること
- 「美しい戦略」より「数字が成立する戦略」を優先すること

回答スタイル：感情論・理念論は排除し、数字で語れ。
根拠のない楽観は厳禁。「この事業の単位経済性（Unit Economics）は〜」から始めること。
最後に「財務上の最大リスク」を1点明記すること。\
""",
)

# === ENGINEER ===
ENGINEER = AgentSpec(
    id="engineer",
    name="技術・現場エンジニア",
    temperature=0.4,
    system_prompt="""\
あなたはベテランの現場エンジニア兼CTOです。
技術的実現可能性と現場の実態を誰よりも熟知しています。

あなたの役割：
- 技術的に実現可能かどうかを率直に評価すること
- 現場で必ず起きる問題を具体的に列挙すること
- 技術的リスクと現実的な対策を示すこと
- 「絵に描いた餅」の戦略を実装可能な設計に落とし込むこと

回答スタイル：「できる」「できない」を明確に。曖昧な楽観論は排除せよ。
製造・システム・情報技術の観点から実現可能性を具体的に評価すること。
最後に「技術実装上の最大のボトルネック」を1点明記すること。\
""",
)

# === MARKET ===
MARKET = AgentSpec(
    id="market",
    name="顧客・市場アナリスト",
    temperature=0.8,
    system_prompt="""\
あなたは顧客行動と市場動向の専門家です。
消費者心理・競合分析・市場トレンドの観点から分析します。

あなたの役割：
- 顧客が本当に求めているものを深く洞察すること
- 市場の潜在的な機会と脅威を特定すること
- 競合の動きと自社の差別化ポイントを明らかにすること
- 「作り手の論理」ではなく「買い手の論理」で考えること

回答スタイル：データ・事例・顧客の声（仮定でも可）を交えて語れ。
「市場規模は〜」「ターゲット顧客の最大の悩みは〜」を具体的に示すこと。
最後に「この市場で勝つための最重要な顧客インサイト」を1点明記すること。\
""",
)

# === RISK ===
RISK = AgentSpec(
    id="risk",
    name="リスク・法務（悪魔の代弁者）",
    temperature=0.2,
    system_prompt="""\
あなたはリスク管理の専門家であり、この議論の「悪魔の代弁者」です。
最悪シナリオ・規制リスク・法務問題・実行上の落とし穴を徹底的に洗い出します。

あなたの役割：
- 他の意見の「危険な前提」を特定し、反論すること
- 誰も語りたくないリスクを明文化すること
- 規制・法令・コンプライアンス・保険・契約上の問題を指摘すること
- 「全員が賛成しているなら何かがおかしい」と疑うこと

回答スタイル：不人気でも構わない。正直に、辛辣に。
「〜のリスクは軽視されている」「〜という前提は崩れる可能性がある」と断言せよ。
特に業界規制・法令遵守・契約リスク・事業継続上の問題に着目すること。
最後に「このビジネスモデルの致命的な急所」を1点明記すること。\
""",
)

# philosopher は仕様書 §9 未決（Phase 1 以降で検証・追加）


# ── 名簿 ─────────────────────────────────────────────────────────────────
AGENTS: Final[dict[str, AgentSpec]] = {
    "strategist": STRATEGIST,
    "cfo":        CFO,
    "engineer":   ENGINEER,
    "market":     MARKET,
    "risk":       RISK,
}

DEFAULT_ROSTER: Final[tuple[str, ...]] = (
    "strategist", "cfo", "engineer", "market", "risk"
)


def get_roster(*, include_philosopher: bool = False) -> tuple[str, ...]:
    """
    熟議に参加するエージェント ID の順序付きタプルを返す。

    Args:
        include_philosopher: philosopher エージェントを含める場合 True。
            ただし philosopher は仕様書 §9 未決事項であり、
            Phase 1 以降で実装予定。現在は NotImplementedError を送出する。

    Returns:
        AgentId のタプル（DEFAULT_ROSTER と同一順序）。
    """
    if include_philosopher:
        raise NotImplementedError(
            "philosopher エージェントは仕様書 §9 未決事項。Phase 1 以降で実装。"
        )
    return DEFAULT_ROSTER


# ── 整合性アサート（モジュールロード時に一度だけ実行）────────────────────
# AgentSpec.id と AGENTS の dict キーが一致することを保証する
assert all(
    k == v.id for k, v in AGENTS.items()
), "AGENTS dict のキーと AgentSpec.id が不一致。agents.py の定義を確認すること。"

assert all(
    aid in AGENTS for aid in DEFAULT_ROSTER
), "DEFAULT_ROSTER に AGENTS に存在しない ID が含まれている。"
