-- =============================================================
-- migration 0009: 研究セッションの user_id NULL 許容化
-- =============================================================
-- create_research_session() は is_research=true で user_id なし（研究バッチ用途）。
-- user_id NOT NULL 制約を DROP し、通常セッション（is_research=false）への必須化は
-- CHECK 制約で維持する。
-- 適用対象: ozbruhuisxkepivcvynx（pr-scribe DB・arif スキーマ）
-- 適用日: 2026-06-25
-- =============================================================

ALTER TABLE arif.deliberation_sessions
    ALTER COLUMN user_id DROP NOT NULL;

ALTER TABLE arif.deliberation_sessions
    ADD CONSTRAINT chk_user_id_required
    CHECK (is_research = true OR user_id IS NOT NULL);
