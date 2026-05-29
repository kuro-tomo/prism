-- =============================================================
-- ARIF migration 0004：会社プロフィール基盤
-- =============================================================
-- 目的:
--   ① company_profile テーブル新設（全熟議への固定注入用）
--   ② company_context.embedding の NULL 防止（NOT NULL 制約は付けないが
--      アプリ側で必ず生成する）
--
-- 設計方針（Opus Phase 5T 設計諮問・2026-05-29）:
--   - 会社の基本前提は RAG ではなく固定注入。課題の類似度に依存しない。
--   - 1 ユーザー = 1 プロフィール（UNIQUE 制約）
--   - フィールドは操作説明書のテンプレートに対応した 7 項目
-- =============================================================

SET search_path TO arif;

-- =============================================================
-- company_profile — 会社基本前提（全熟議に固定注入）
-- =============================================================
CREATE TABLE IF NOT EXISTS arif.company_profile (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL UNIQUE REFERENCES auth.users(id),

    -- 操作説明書テンプレートに対応する 7 フィールド
    industry        TEXT NOT NULL DEFAULT '',   -- 業種・事業内容
    scale           TEXT NOT NULL DEFAULT '',   -- 規模（売上・従業員等）
    main_products   TEXT NOT NULL DEFAULT '',   -- 主力製品・サービス
    main_customers  TEXT NOT NULL DEFAULT '',   -- 主要顧客・販売先
    strengths       TEXT NOT NULL DEFAULT '',   -- 現在の状況・強み
    avoid_directions TEXT NOT NULL DEFAULT '',  -- 避けたい方向
    free_context    TEXT NOT NULL DEFAULT '',   -- その他の文脈（自由記述）

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER arif_profile_updated_at
    BEFORE UPDATE ON arif.company_profile
    FOR EACH ROW EXECUTE FUNCTION arif.update_updated_at();

-- RLS
ALTER TABLE arif.company_profile ENABLE ROW LEVEL SECURITY;
CREATE POLICY "arif_user_own_profile" ON arif.company_profile
    FOR ALL TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- service_role は RLS をバイパスするため arif_service ロールからも操作可
GRANT ALL ON arif.company_profile TO arif_service;
