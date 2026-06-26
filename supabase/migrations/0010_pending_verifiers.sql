-- 0010: PKCE verifier DB永続化（Render Free スピンダウン対策）
-- magic link 送信時に verifier を保存し、コールドスタート後のコールバックでも復元できるようにする

CREATE TABLE IF NOT EXISTS arif.pending_verifiers (
    verifier  text        PRIMARY KEY,
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '30 minutes')
);

GRANT SELECT, INSERT, DELETE ON arif.pending_verifiers TO arif_service;
