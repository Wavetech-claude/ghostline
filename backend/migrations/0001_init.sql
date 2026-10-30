-- Ghostline 0001: the account that may sign in, its sessions, and the apps registered for push.
CREATE TABLE IF NOT EXISTS owner_account (
    sub              text        PRIMARY KEY,          -- Apple's stable user id for this app
    email            text,                             -- the Apple ID email, or Apple's relay address
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_sign_in_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    jti         uuid        PRIMARY KEY,
    sub         text        NOT NULL REFERENCES owner_account (sub),
    platform    text        NOT NULL CHECK (platform IN ('ios', 'macos')),
    issued_at   timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    revoked     boolean     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS sessions_live ON sessions (sub) WHERE NOT revoked;

CREATE TABLE IF NOT EXISTS devices (
    token          text        PRIMARY KEY,            -- the APNs device token
    platform       text        NOT NULL CHECK (platform IN ('ios', 'macos')),
    environment    text        NOT NULL CHECK (environment IN ('sandbox', 'production')),
    sub            text        NOT NULL REFERENCES owner_account (sub),
    registered_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now()
);
