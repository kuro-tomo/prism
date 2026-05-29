-- =============================================================
-- ARIF migration 0005：会社プロフィールに website_url カラム追加
-- =============================================================
-- 目的: F-020 Webサイト自動読み込み機能のための URL 保存先
-- 設計書 §6.5 参照
--
-- 適用済み: ozbruhuisxkepivcvynx（pr-scribe DB・arif スキーマ）
-- 適用日: 2026-05-29
-- =============================================================

SET search_path TO arif;

ALTER TABLE arif.company_profile
    ADD COLUMN IF NOT EXISTS website_url TEXT NOT NULL DEFAULT '';
