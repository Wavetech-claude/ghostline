-- Ghostline 0002: the ledger (books, cash flows, trades, option events, attachments) and the risk rules.
-- Money is numeric, never float; quantities allow fractions; nothing here holds a fixed dollar amount.

CREATE TABLE IF NOT EXISTS books (
    id          serial      PRIMARY KEY,
    kind        text        NOT NULL UNIQUE CHECK (kind IN ('real', 'practice', 'ai', 'ghost')),
    name        text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO books (kind, name) VALUES
    ('real', 'Real'), ('practice', 'Practice'), ('ai', 'AI trader'), ('ghost', 'S&P 500 ghost')
ON CONFLICT (kind) DO NOTHING;

CREATE TABLE IF NOT EXISTS cash_flows (
    id          bigserial   PRIMARY KEY,
    book_id     integer     NOT NULL REFERENCES books (id),
    amount      numeric(18, 2) NOT NULL CHECK (amount <> 0),   -- positive = deposit, negative = withdrawal
    at          timestamptz NOT NULL,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cash_flows_book_at ON cash_flows (book_id, at);

CREATE TABLE IF NOT EXISTS trades (
    id             bigserial   PRIMARY KEY,
    book_id        integer     NOT NULL REFERENCES books (id),
    ticker         text        NOT NULL,
    asset          text        NOT NULL CHECK (asset IN ('stock', 'option')),
    side           text        NOT NULL CHECK (side IN ('buy', 'sell', 'sell_to_open', 'buy_to_close')),
    quantity       numeric(18, 6) NOT NULL CHECK (quantity > 0),
    fill           numeric(18, 4) NOT NULL CHECK (fill >= 0),
    at             timestamptz NOT NULL,
    fees           numeric(18, 2) NOT NULL DEFAULT 0 CHECK (fees >= 0),
    broker         text,
    note           text,
    option_symbol  text,                          -- options: the OCC symbol
    lane           text        CHECK (lane IS NULL OR lane IN ('long', 'swing', 'puts', 'day')),
    idea_id        bigint,                        -- phase 4: ideas(id)
    memo_id        bigint,                        -- phase 5: memos(id)
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK ((asset = 'stock' AND side IN ('buy', 'sell')) OR (asset = 'option'))
);
CREATE INDEX IF NOT EXISTS trades_book_ticker_at ON trades (book_id, ticker, at);

CREATE TABLE IF NOT EXISTS option_events (
    id          bigserial   PRIMARY KEY,
    trade_id    bigint      NOT NULL REFERENCES trades (id) ON DELETE CASCADE,
    kind        text        NOT NULL CHECK (kind IN ('expiry', 'assignment', 'early_close')),
    at          timestamptz NOT NULL,
    strike      numeric(18, 4),
    shares      numeric(18, 6),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attachments (
    id            bigserial   PRIMARY KEY,
    trade_id      bigint      NOT NULL REFERENCES trades (id) ON DELETE CASCADE,
    object_key    text        NOT NULL UNIQUE,
    content_type  text        NOT NULL,
    bytes         bigint      NOT NULL CHECK (bytes > 0),
    filename      text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- The owner's risk rules: a share of the balance, a count or a switch, never a dollar amount. Until a row exists for
-- every key, no idea passes (ghostline.risk). A rule switched off does not apply.
CREATE TABLE IF NOT EXISTS risk_rules (
    key         text        PRIMARY KEY,
    value       numeric(18, 6) NOT NULL CHECK (value >= 0),
    enabled     boolean     NOT NULL DEFAULT true,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS risk_rule_changes (
    id           bigserial   PRIMARY KEY,
    key          text        NOT NULL,
    old_value    numeric(18, 6),
    old_enabled  boolean,
    new_value    numeric(18, 6) NOT NULL,
    new_enabled  boolean     NOT NULL,
    changed_by   text        NOT NULL,          -- the owner's Apple sub
    at           timestamptz NOT NULL DEFAULT now()
);

-- Every pass or refusal, for the journal (phase 4 fills subject_kind idea | ai_trade | invest_split).
CREATE TABLE IF NOT EXISTS risk_decisions (
    id            bigserial   PRIMARY KEY,
    subject_kind  text        NOT NULL,
    subject_id    bigint,
    result        text        NOT NULL CHECK (result IN ('pass', 'refuse')),
    rule          text,
    reason        text        NOT NULL,
    at            timestamptz NOT NULL DEFAULT now()
);

-- Net stock quantity per book and ticker (options are closed by their own events, phase 3).
CREATE OR REPLACE VIEW positions AS
SELECT book_id, ticker,
       SUM(CASE WHEN side = 'buy' THEN quantity ELSE -quantity END) AS quantity
FROM trades
WHERE asset = 'stock'
GROUP BY book_id, ticker
HAVING SUM(CASE WHEN side = 'buy' THEN quantity ELSE -quantity END) <> 0;
