-- 0006_session_embedding.sql
-- 熟議間RAG（意味検索）— deliberation_sessions に question_embedding 追加
--
-- 設計書 §6.3 RAG戦略ステップ⑤（仕様書 F-021）
-- Opus設計諮問（2026-06-16）により以下を確定:
--   - 生成タイミング: セッション完了時（save_debate_result内）
--   - インデックス: 1万件未満は逐次スキャンで十分。追加しない
--   - 検索条件: status='completed' AND question_embedding IS NOT NULL

ALTER TABLE arif.deliberation_sessions
    ADD COLUMN IF NOT EXISTS question_embedding vector(1024);
