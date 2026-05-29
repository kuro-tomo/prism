"""
ARIF API — Pydantic スキーマ定義

engine/ の dataclass とは分離した HTTP 境界の型。
設計書 §7・仕様書 F-009 に準拠。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ===========================
# 熟議（deliberation）
# ===========================

class DeliberationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000, description="経営課題のテキスト（仕様書 F-001）")
    title: str = Field(min_length=1, max_length=200, description="セッションの表題")
    mode: Literal["speed", "standard", "deep"] = Field(
        default="standard",
        description="熟議モード（早足/常足/熟考）—— 仕様書 F-009",
    )
    include_philosopher: bool = Field(default=False, description="哲学エージェントを追加するか")
    background: bool = Field(default=False, description="バックグラウンド実行（仕様書 F-013）")


class DeliberationResponse(BaseModel):
    session_id: UUID
    status: str
    mode: str
    estimated_seconds: int
    stream_url: str


class AgentResponseOut(BaseModel):
    agent_id: str
    agent_role: str
    round: int
    content: str
    key_points: list[dict[str, Any]]
    stance: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None


class DeliberationDetail(BaseModel):
    session_id: UUID
    title: str
    question: str
    mode: str
    status: str
    third_solution: dict[str, Any] | None
    total_cost_usd: float
    duration_seconds: int | None
    created_at: datetime
    agent_responses: list[AgentResponseOut]


class SessionListItem(BaseModel):
    session_id: UUID
    title: str
    mode: str
    status: str
    total_cost_usd: float
    created_at: datetime


# ===========================
# フィードバック（仕様書 B-001〜B-003）
# ===========================

class FeedbackRequest(BaseModel):
    overall_rating: int = Field(ge=1, le=5)
    usefulness: int = Field(ge=1, le=5)
    novelty: int = Field(ge=1, le=5)
    best_agent: str | None = None
    worst_agent: str | None = None
    free_comment: str | None = None
    action_taken: str | None = None  # 実際の意思決定内容（後日入力・B-003）


class FeedbackResponse(BaseModel):
    feedback_id: UUID
    session_id: UUID


# ===========================
# 会社コンテキスト（仕様書 M-003・M-004）
# ===========================

class ContextEntryRequest(BaseModel):
    category: Literal["strategy", "financial", "org", "market", "risk", "history"]
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    source: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None


class ContextEntryResponse(BaseModel):
    context_id: UUID
    category: str
    title: str
    created_at: datetime


class ContextListItem(BaseModel):
    context_id: UUID
    category: str
    title: str
    source: str | None
    valid_from: date | None
    valid_until: date | None
    created_at: datetime


# ===========================
# 会社プロフィール（Phase 5T・固定注入用）
# ===========================

class CompanyProfileRequest(BaseModel):
    website_url:      str = Field(default="", max_length=2048, description="会社WebサイトURL（自動読み込みに使用）")
    industry:         str = Field(default="", max_length=500, description="業種・事業内容")
    scale:            str = Field(default="", max_length=500, description="規模（売上・従業員等）")
    main_products:    str = Field(default="", max_length=500, description="主力製品・サービス")
    main_customers:   str = Field(default="", max_length=500, description="主要顧客・販売先")
    strengths:        str = Field(default="", max_length=500, description="現在の状況・強み")
    avoid_directions: str = Field(default="", max_length=500, description="避けたい方向")
    free_context:     str = Field(default="", max_length=1000, description="その他の文脈（自由記述）")


class CompanyProfileResponse(BaseModel):
    website_url:      str = ""
    industry:         str
    scale:            str
    main_products:    str
    main_customers:   str
    strengths:        str
    avoid_directions: str
    free_context:     str
    updated_at: datetime | None = None


class WebsiteFetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048, description="会社WebサイトURL")


class WebsiteFetchResponse(BaseModel):
    """Webサイトから抽出した会社情報候補（保存前の確認用・F-020）。"""
    industry:       str = ""
    main_products:  str = ""
    main_customers: str = ""
    strengths:      str = ""
    error:          str = ""  # 取得・抽出失敗時のメッセージ


# ===========================
# 認証（仕様書 S-004）
# ===========================

class MagicLinkRequest(BaseModel):
    email: str = Field(description="Magic Link 送信先メールアドレス")


class MagicLinkResponse(BaseModel):
    message: str = "Magic Link を送信しました。メールをご確認ください。"


# ===========================
# 汎用
# ===========================

class HealthResponse(BaseModel):
    status: str
    version: str = "0.9.0"
