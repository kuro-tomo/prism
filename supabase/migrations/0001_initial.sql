-- =============================================================
-- ARIF Phase 2 初期スキーマ（arif PostgreSQL スキーマ）
-- =============================================================
-- pr-scribe の Supabase DB 内に arif ネームスペースとして共存。
-- 設計書 §6・仕様書 M-001〜M-005 に基づく。
--
-- 適用済み: ozbruhuisxkepivcvynx（pr-scribe DB）
-- 適用日: 2026-05-27
-- =============================================================

-- arif スキーマを作成
CREATE SCHEMA IF NOT EXISTS arif;

SET search_path TO arif;

-- pgvector 拡張（DB レベルでグローバル）
CREATE EXTENSION IF NOT EXISTS vector;


-- =============================================================
-- updated_at 自動更新トリガー関数
-- =============================================================
CREATE OR REPLACE FUNCTION arif.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- =============================================================
-- deliberation_sessions — 熟議セッション（仕様書 M-001）
-- =============================================================
CREATE TABLE IF NOT EXISTS arif.deliberation_sessions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title               TEXT NOT NULL,
    question            TEXT NOT NULL,
    mode                TEXT NOT NULL DEFAULT 'standard',
    -- speed / standard / deep（仕様書 F-009）
    status              TEXT NOT NULL DEFAULT 'pending',
    -- pending / round1 / round2 / synthesis / pre_mortem / completed / failed

    -- ThirdSolution 全体を JSONB で保存（仕様書 §4.4・設計書 §5.4 対応）
    third_solution      JSONB,

    -- 実行メタデータ
    total_input_tokens  INTEGER     NOT NULL DEFAULT 0,
    total_output_tokens INTEGER     NOT NULL DEFAULT 0,
    total_cost_usd      DECIMAL(10,4) NOT NULL DEFAULT 0,
    duration_seconds    INTEGER,
    error_message       TEXT,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER sessions_updated_at
    BEFORE UPDATE ON arif.deliberation_sessions
    FOR EACH ROW EXECUTE FUNCTION arif.update_updated_at();


-- =============================================================
-- agent_responses — エージェント発言ログ
-- =============================================================
CREATE TABLE IF NOT EXISTS arif.agent_responses (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID NOT NULL REFERENCES arif.deliberation_sessions(id) ON DELETE CASCADE,

    round       INTEGER NOT NULL CHECK (round BETWEEN 1 AND 3),
    agent_id    TEXT    NOT NULL,
    agent_role  TEXT    NOT NULL,
    model_used  TEXT    NOT NULL,
    temperature DECIMAL(3,2),

    content     TEXT    NOT NULL,
    key_points  JSONB   NOT NULL DEFAULT '[]',
    stance      TEXT    CHECK (stance IN ('support','oppose','neutral','conditional')),
    confidence  DECIMAL(3,2) CHECK (confidence BETWEEN 0 AND 1),

    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        DECIMAL(10,6),
    latency_ms      INTEGER,
    error           TEXT,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_arif_unique_response
    ON arif.agent_responses(session_id, round, agent_id);
CREATE INDEX idx_arif_agent_responses_session
    ON arif.agent_responses(session_id);


-- =============================================================
-- company_context — 会社コンテキスト（仕様書 M-003, M-004）
-- =============================================================
CREATE TABLE IF NOT EXISTS arif.company_context (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category    TEXT NOT NULL,
    -- strategy / financial / org / market / risk / history
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1536),   -- OpenAI text-embedding-3-small（仕様書 §7）
    source      TEXT,
    valid_from  DATE,
    valid_until DATE,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER arif_context_updated_at
    BEFORE UPDATE ON arif.company_context
    FOR EACH ROW EXECUTE FUNCTION arif.update_updated_at();


-- =============================================================
-- deliberation_insights — セッション横断の洞察（仕様書 M-005）
-- =============================================================
CREATE TABLE IF NOT EXISTS arif.deliberation_insights (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES arif.deliberation_sessions(id) ON DELETE CASCADE,
    insight_type    TEXT NOT NULL,
    -- decision / concern / pattern / principle
    content         TEXT NOT NULL,
    embedding       vector(1536),

    president_rating    INTEGER CHECK (president_rating BETWEEN 1 AND 5),
    president_note      TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_arif_insights_session
    ON arif.deliberation_insights(session_id);


-- =============================================================
-- session_feedback — 社長フィードバック（仕様書 B-001〜B-003）
-- =============================================================
CREATE TABLE IF NOT EXISTS arif.session_feedback (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES arif.deliberation_sessions(id) ON DELETE CASCADE,
    overall_rating  INTEGER CHECK (overall_rating BETWEEN 1 AND 5),
    usefulness      INTEGER CHECK (usefulness BETWEEN 1 AND 5),
    novelty         INTEGER CHECK (novelty BETWEEN 1 AND 5),
    best_agent      TEXT,
    worst_agent     TEXT,
    free_comment    TEXT,
    action_taken    TEXT,   -- 実際にどう意思決定したか（後日入力・B-003）

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_arif_feedback_session
    ON arif.session_feedback(session_id);


-- =============================================================
-- pgvector インデックス（IVFFlat・コサイン類似度）
-- =============================================================
CREATE INDEX idx_arif_context_embedding ON arif.company_context
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX idx_arif_insights_embedding ON arif.deliberation_insights
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);


-- =============================================================
-- RLS（Row Level Security・仕様書 S-004）
-- 認証方式：Supabase Auth + Magic Link（仕様書 §9 確定）
-- =============================================================
ALTER TABLE arif.deliberation_sessions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE arif.agent_responses          ENABLE ROW LEVEL SECURITY;
ALTER TABLE arif.company_context          ENABLE ROW LEVEL SECURITY;
ALTER TABLE arif.deliberation_insights    ENABLE ROW LEVEL SECURITY;
ALTER TABLE arif.session_feedback         ENABLE ROW LEVEL SECURITY;

CREATE POLICY "arif_authenticated_full_access" ON arif.deliberation_sessions
    FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "arif_authenticated_full_access" ON arif.agent_responses
    FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "arif_authenticated_full_access" ON arif.company_context
    FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "arif_authenticated_full_access" ON arif.deliberation_insights
    FOR ALL TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "arif_authenticated_full_access" ON arif.session_feedback
    FOR ALL TO authenticated USING (true) WITH CHECK (true);
