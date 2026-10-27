CREATE TABLE feed (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    dhan_client_id TEXT,
    access_token_enc TEXT,
    status TEXT NOT NULL CHECK (status IN ('unset', 'active', 'expired')),
    issued_at TEXT,
    expires_at TEXT,
    token_validity TEXT,
    data_plan TEXT,
    data_validity TEXT,
    last_ok_at TEXT,
    last_error TEXT,
    expiry_mail_sent_for TEXT,
    warning_mail_sent_for TEXT,
    updated_at TEXT NOT NULL
);
INSERT INTO feed (id, status, updated_at) VALUES (1, 'unset', '1970-01-01T00:00:00+00:00');

CREATE TABLE watchers (
    id TEXT PRIMARY KEY,
    notify_email TEXT NOT NULL,
    symbol TEXT NOT NULL,
    label TEXT NOT NULL,
    script TEXT NOT NULL,
    market TEXT NOT NULL,
    check_every_s INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ARMED', 'PAUSED', 'ERROR', 'EXPIRED', 'CANCELLED')),
    status_reason TEXT,
    paused_by TEXT CHECK (paused_by IN ('user', 'feed')),
    baseline_done INTEGER NOT NULL DEFAULT 0,
    latched INTEGER NOT NULL DEFAULT 0,
    last_met INTEGER,
    last_value TEXT,
    last_detail TEXT,
    last_error TEXT,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT,
    last_fired_at TEXT,
    fired_count INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX watchers_due_idx ON watchers (status, last_checked_at);

CREATE TABLE watcher_fires (
    id TEXT PRIMARY KEY,
    watcher_id TEXT NOT NULL REFERENCES watchers (id) ON DELETE CASCADE,
    fired_at TEXT NOT NULL,
    value TEXT,
    detail TEXT,
    emailed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX watcher_fires_watcher_idx ON watcher_fires (watcher_id, fired_at DESC);
