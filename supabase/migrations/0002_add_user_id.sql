-- =============================================================
-- ARIF migration 0002：user_id カラム追加・RLS 強化
-- =============================================================
-- Opus 設計諮問（Phase 3）にて確定：
-- Phase 5 マルチユーザー拡張を見据え、今のうちに user_id を全テーブルに追加。
-- RLS を auth.uid() = user_id に強化することで情報分離を保証。
-- agent_responses は session_id 経由で間接的に RLS 適用ゆえ対象外。
--
-- 適用済み: ozbruhuisxkepivcvynx（pr-scribe DB・arif スキーマ）
-- 適用日: 2026-05-28
-- =============================================================

SET search_path TO arif;

-- =============================================================
-- deliberation_sessions に user_id 追加
-- =============================================================
ALTER TABLE arif.deliberation_sessions
    ADD COLUMN user_id UUID NOT NULL REFERENCES auth.users(id);

CREATE INDEX idx_arif_sessions_user_id
    ON arif.deliberation_sessions(user_id);

-- 既存ポリシーを DROP し、user_id 条件付きポリシーに差し替え
DROP POLICY IF EXISTS "arif_authenticated_full_access" ON arif.deliberation_sessions;
CREATE POLICY "arif_user_own_sessions" ON arif.deliberation_sessions
    FOR ALL TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- =============================================================
-- company_context に user_id 追加
-- =============================================================
ALTER TABLE arif.company_context
    ADD COLUMN user_id UUID NOT NULL REFERENCES auth.users(id);

DROP POLICY IF EXISTS "arif_authenticated_full_access" ON arif.company_context;
CREATE POLICY "arif_user_own_context" ON arif.company_context
    FOR ALL TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- =============================================================
-- deliberation_insights に user_id 追加
-- =============================================================
ALTER TABLE arif.deliberation_insights
    ADD COLUMN user_id UUID NOT NULL REFERENCES auth.users(id);

DROP POLICY IF EXISTS "arif_authenticated_full_access" ON arif.deliberation_insights;
CREATE POLICY "arif_user_own_insights" ON arif.deliberation_insights
    FOR ALL TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- =============================================================
-- session_feedback に user_id 追加
-- =============================================================
ALTER TABLE arif.session_feedback
    ADD COLUMN user_id UUID NOT NULL REFERENCES auth.users(id);

DROP POLICY IF EXISTS "arif_authenticated_full_access" ON arif.session_feedback;
CREATE POLICY "arif_user_own_feedback" ON arif.session_feedback
    FOR ALL TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- =============================================================
-- agent_responses は session_id FK 経由で間接的に RLS 適用。
-- ポリシーは "session のオーナーのみ参照可" に強化。
-- =============================================================
DROP POLICY IF EXISTS "arif_authenticated_full_access" ON arif.agent_responses;
CREATE POLICY "arif_user_own_responses" ON arif.agent_responses
    FOR ALL TO authenticated
    USING (
        session_id IN (
            SELECT id FROM arif.deliberation_sessions
            WHERE user_id = auth.uid()
        )
    )
    WITH CHECK (
        session_id IN (
            SELECT id FROM arif.deliberation_sessions
            WHERE user_id = auth.uid()
        )
    );
