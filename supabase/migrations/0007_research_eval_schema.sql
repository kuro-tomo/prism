-- =============================================================
-- ARIF migration 0007：研究評価スキーマ（CFIM 論文用）
-- =============================================================
-- Opus 4.8 設計諮問（2026-06-24）により確定。
-- completeness critic 指摘4件を反映済み：
--   (A) SECURITY DEFINER 関数の REVOKE FROM PUBLIC を明示
--   (B) research_ratings_long を LEFT JOIN に変更（未評価ランを落とさない）
--   (C) research_fes_summary の GROUP BY から run_no を除去
--   (D) phi_r2 / fes_score / alpha / beta / gamma に CHECK 制約追加
--
-- 適用対象: ozbruhuisxkepivcvynx（pr-scribe DB・arif スキーマ）
-- 適用日: 2026-06-24
-- =============================================================

SET search_path TO arif;


-- =============================================================
-- deliberation_sessions に研究用カラム追加（仕様書 F-022・設計書 §6.6）
-- =============================================================
-- diversity_score_r1/r2: CFIM Φ 干渉ポテンシャル。SSE パスは _persist_round_summary()・バッチパスは save_debate_result() で永続化。
-- is_research: 研究実験セッションを本番セッションと分離し RAG 汚染を防ぐ。
ALTER TABLE arif.deliberation_sessions
    ADD COLUMN IF NOT EXISTS diversity_score_r1 REAL
        CHECK (diversity_score_r1 BETWEEN 0 AND 1),
    ADD COLUMN IF NOT EXISTS diversity_score_r2 REAL
        CHECK (diversity_score_r2 BETWEEN 0 AND 1),
    ADD COLUMN IF NOT EXISTS is_research BOOLEAN NOT NULL DEFAULT false;

-- 研究セッション専用インデックス（RAG フィルタの高速化）
CREATE INDEX IF NOT EXISTS idx_arif_sessions_is_research
    ON arif.deliberation_sessions(is_research)
    WHERE is_research = true;


-- =============================================================
-- eval_scenarios — 標準化シナリオ集（50件・設計書 §6.6.2）
-- =============================================================
CREATE TABLE IF NOT EXISTS arif.eval_scenarios (
    scenario_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title        TEXT        NOT NULL,
    background   TEXT        NOT NULL,    -- 5〜8行の経営背景
    question     TEXT        NOT NULL,    -- 1行の熟議課題
    category     TEXT        NOT NULL
        CHECK (category IN (
            'strategy','financial','org','market','risk','hr','digital','other'
        )),
    is_active    BOOLEAN     NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- =============================================================
-- eval_runs — 評価実行ログ（300件: 50シナリオ×3反復×2条件）
-- =============================================================
CREATE TABLE IF NOT EXISTS arif.eval_runs (
    run_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    scenario_id  UUID        NOT NULL
        REFERENCES arif.eval_scenarios(scenario_id) ON DELETE CASCADE,
    session_id   UUID        NOT NULL
        REFERENCES arif.deliberation_sessions(id) ON DELETE CASCADE,
    condition    TEXT        NOT NULL
        CHECK (condition IN ('prism', 'single_opus')),
    run_no       INTEGER     NOT NULL
        CHECK (run_no BETWEEN 1 AND 3),
    -- 注：評価者情報は eval_ratings.evaluator_id に正規化（1 run を複数評価者が採点）。
    --     run は機械実行ログ（compute_fes は決定論的）ゆえ run 単位の評価者列は持たぬ。

    -- FES コンポーネント（CFIM §9.2 Definition 9.1）
    fes_score    REAL        CHECK (fes_score    BETWEEN 0 AND 1),
    g_orig       INTEGER     CHECK (g_orig       BETWEEN 1 AND 5),
    phi_r1       REAL        CHECK (phi_r1       BETWEEN 0 AND 1),
    phi_r2       REAL        CHECK (phi_r2       BETWEEN 0 AND 1),
    novelty_flag BOOLEAN,

    -- 係数スナップショット（係数変更時の再現性確保）
    alpha        REAL        NOT NULL DEFAULT 0.4
        CHECK (alpha  BETWEEN 0 AND 1),
    beta         REAL        NOT NULL DEFAULT 0.4
        CHECK (beta   BETWEEN 0 AND 1),
    gamma        REAL        NOT NULL DEFAULT 0.2
        CHECK (gamma  BETWEEN 0 AND 1),

    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 同一シナリオ・条件・反復番号の重複を防ぐ
    UNIQUE (scenario_id, condition, run_no)
);

CREATE INDEX IF NOT EXISTS idx_arif_eval_runs_scenario
    ON arif.eval_runs(scenario_id);
CREATE INDEX IF NOT EXISTS idx_arif_eval_runs_session
    ON arif.eval_runs(session_id);


-- =============================================================
-- eval_ratings — 人間評価者のスコア（ICC / Fleiss' κ 用長形式）
-- =============================================================
CREATE TABLE IF NOT EXISTS arif.eval_ratings (
    rating_id        UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id           UUID    NOT NULL
        REFERENCES arif.eval_runs(run_id) ON DELETE CASCADE,
    evaluator_id     TEXT    NOT NULL,
    rating_dimension TEXT    NOT NULL
        CHECK (rating_dimension IN (
            'usefulness', 'novelty', 'specificity', 'feasibility'
        )),
    score            INTEGER NOT NULL
        CHECK (score BETWEEN 1 AND 5),
    note             TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 同一評価者が同一ラン×次元を重複評価しない
    UNIQUE (run_id, evaluator_id, rating_dimension)
);

CREATE INDEX IF NOT EXISTS idx_arif_eval_ratings_run
    ON arif.eval_ratings(run_id);


-- =============================================================
-- RLS — 研究テーブルは service_role のみアクセス可
-- （認証済みユーザーからの直接参照を封鎖）
-- =============================================================
ALTER TABLE arif.eval_scenarios ENABLE ROW LEVEL SECURITY;
ALTER TABLE arif.eval_runs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE arif.eval_ratings   ENABLE ROW LEVEL SECURITY;

-- CREATE POLICY は IF NOT EXISTS 構文を持たぬため DROP POLICY IF EXISTS を前置（0002 と同パターン・再適用冪等化）
DROP POLICY IF EXISTS "eval_scenarios_service_only" ON arif.eval_scenarios;
CREATE POLICY "eval_scenarios_service_only" ON arif.eval_scenarios
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "eval_runs_service_only" ON arif.eval_runs;
CREATE POLICY "eval_runs_service_only" ON arif.eval_runs
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "eval_ratings_service_only" ON arif.eval_ratings;
CREATE POLICY "eval_ratings_service_only" ON arif.eval_ratings
    FOR ALL TO service_role USING (true) WITH CHECK (true);


-- =============================================================
-- SECURITY DEFINER 集計関数（設計書 §6.6.3）
-- REVOKE FROM PUBLIC 必須（PostgreSQL デフォルトは PUBLIC に EXECUTE を付与）
-- =============================================================

-- research_fes_summary — シナリオ×条件ごとの FES 統計
-- run_no は GROUP BY に含めない（50シナリオ×2条件の行列が目的）
CREATE OR REPLACE FUNCTION arif.research_fes_summary()
RETURNS TABLE(
    scenario_id  UUID,
    condition    TEXT,
    n_runs       BIGINT,
    mean_fes     NUMERIC,
    sd_fes       NUMERIC
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = arif
AS $$
    SELECT
        er.scenario_id,
        er.condition,
        COUNT(*)                              AS n_runs,
        ROUND(AVG(er.fes_score)::numeric, 4) AS mean_fes,
        ROUND(STDDEV(er.fes_score)::numeric, 4) AS sd_fes
    FROM arif.eval_runs er
    WHERE er.fes_score IS NOT NULL
    GROUP BY er.scenario_id, er.condition
    ORDER BY er.scenario_id, er.condition;
$$;

REVOKE ALL   ON FUNCTION arif.research_fes_summary() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION arif.research_fes_summary() TO service_role;


-- research_ratings_long — ICC / Fleiss' κ 用の長形式評価データ
-- LEFT JOIN: 未評価ランも行として残す（INNER JOIN だと未評価ランが消えて集計が歪む）
CREATE OR REPLACE FUNCTION arif.research_ratings_long()
RETURNS TABLE(
    run_id           UUID,
    scenario_id      UUID,
    condition        TEXT,
    run_no           INTEGER,
    evaluator_id     TEXT,
    rating_dimension TEXT,
    score            INTEGER
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = arif
AS $$
    SELECT
        ev.run_id,
        ev.scenario_id,
        ev.condition,
        ev.run_no,
        er.evaluator_id,
        er.rating_dimension,
        er.score
    FROM arif.eval_runs ev
    LEFT JOIN arif.eval_ratings er ON er.run_id = ev.run_id
    ORDER BY ev.scenario_id, ev.condition, ev.run_no,
             er.evaluator_id, er.rating_dimension;
$$;

REVOKE ALL   ON FUNCTION arif.research_ratings_long() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION arif.research_ratings_long() TO service_role;
