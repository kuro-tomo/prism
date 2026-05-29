-- 0003_voyage_embedding.sql
-- Voyage AI voyage-3 への切り替えに伴う embedding 次元変更（1536 → 1024）
--
-- 前提：テーブルは空のため data backfill 不要
-- 手順：インデックス削除 → カラム再定義 → インデックス再作成

-- ─────────────────────────────────────────────
-- company_context
-- ─────────────────────────────────────────────
DROP INDEX IF EXISTS arif.idx_arif_context_embedding;

ALTER TABLE arif.company_context DROP COLUMN IF EXISTS embedding;
ALTER TABLE arif.company_context ADD COLUMN embedding vector(1024);

CREATE INDEX idx_arif_context_embedding ON arif.company_context
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ─────────────────────────────────────────────
-- deliberation_insights
-- ─────────────────────────────────────────────
DROP INDEX IF EXISTS arif.idx_arif_insights_embedding;

ALTER TABLE arif.deliberation_insights DROP COLUMN IF EXISTS embedding;
ALTER TABLE arif.deliberation_insights ADD COLUMN embedding vector(1024);

CREATE INDEX idx_arif_insights_embedding ON arif.deliberation_insights
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
