-- Ghostline 0010: reconciliation (the owner's design, 2026-10-30). Two things are new: the owner's "no" to a suggestion
-- (an idea or a memo's buy call), with an optional reason, and the broker statements the owner enters to check the real
-- book. Everything else is read from what is stored: a taken suggestion is a real trade carrying its idea or memo.

CREATE TABLE IF NOT EXISTS suggestion_decisions (
    kind        text        NOT NULL CHECK (kind IN ('idea', 'memo')),
    ref_id      bigint      NOT NULL,
    outcome     text        NOT NULL DEFAULT 'declined' CHECK (outcome IN ('declined')),
    reason      text,
    decided_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, ref_id)
);

CREATE TABLE IF NOT EXISTS broker_statements (
    id          bigserial   PRIMARY KEY,
    as_of       date        NOT NULL,
    cash        numeric(18, 2) NOT NULL,
    positions   jsonb       NOT NULL,                  -- [{"ticker": "AAPL", "quantity": "10"}], stocks
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS broker_statements_as_of ON broker_statements (as_of DESC, id DESC);
