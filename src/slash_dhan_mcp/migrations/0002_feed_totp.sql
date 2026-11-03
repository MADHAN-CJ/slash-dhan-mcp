-- Optional TOTP auto-renew mode for the feed (P9). `manual` keeps the daily token push.
ALTER TABLE feed ADD COLUMN mode TEXT NOT NULL DEFAULT 'manual' CHECK (mode IN ('manual', 'totp'));
ALTER TABLE feed ADD COLUMN pin_enc TEXT;
ALTER TABLE feed ADD COLUMN totp_secret_enc TEXT;
ALTER TABLE feed ADD COLUMN renew_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE feed ADD COLUMN last_renew_attempt_at TEXT;
ALTER TABLE feed ADD COLUMN last_renewed_at TEXT;
ALTER TABLE feed ADD COLUMN renew_mail_sent_for TEXT;
