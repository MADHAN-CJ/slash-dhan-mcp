-- Automatic token renewal: a pushed Dhan-Web token is chained through GET /v2/RenewToken.
ALTER TABLE feed ADD COLUMN renew_after TEXT;
ALTER TABLE feed ADD COLUMN last_renew_attempt_at TEXT;
ALTER TABLE feed ADD COLUMN last_renewed_at TEXT;
ALTER TABLE feed ADD COLUMN renew_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE feed ADD COLUMN last_renew_error TEXT;
