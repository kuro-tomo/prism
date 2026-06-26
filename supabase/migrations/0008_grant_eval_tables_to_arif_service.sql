-- =============================================================
-- migration 0008: arif_service に eval テーブルの GRANT を付与
-- =============================================================
-- migration 0007 で RLS ポリシーを service_role のみに設定したが、
-- バックエンド接続ロール arif_service（BYPASSRLS=true）への GRANT が欠落していた。
-- 適用対象: ozbruhuisxkepivcvynx（pr-scribe DB・arif スキーマ）
-- 適用日: 2026-06-25
-- =============================================================

GRANT SELECT, INSERT, UPDATE, DELETE ON arif.eval_scenarios TO arif_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON arif.eval_runs      TO arif_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON arif.eval_ratings   TO arif_service;
