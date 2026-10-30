-- Ghostline 0004: the practice account. Order tickets and their fills, next-day settlement (a cash account:
-- unsettled proceeds cannot be spent), option-chain snapshots, and the practice settings (fees, default 0).

CREATE TABLE IF NOT EXISTS orders (
    id             bigserial   PRIMARY KEY,
    book_id        integer     NOT NULL REFERENCES books (id),
    ticker         text        NOT NULL,                      -- the underlying for an option
    asset          text        NOT NULL CHECK (asset IN ('stock', 'option')),
    side           text        NOT NULL CHECK (side IN ('buy', 'sell', 'sell_to_open', 'buy_to_close')),
    type           text        NOT NULL CHECK (type IN ('market', 'limit', 'stop', 'stop_limit')),
    tif            text        NOT NULL DEFAULT 'day' CHECK (tif IN ('day', 'gtc')),
    quantity       numeric(18, 6) NOT NULL CHECK (quantity > 0),  -- shares (fractions allowed) or contracts
    limit_price    numeric(18, 4) CHECK (limit_price IS NULL OR limit_price > 0),
    stop_price     numeric(18, 4) CHECK (stop_price IS NULL OR stop_price > 0),
    stop_loss      numeric(18, 4) CHECK (stop_loss IS NULL OR stop_loss > 0),   -- the risk check's worst case
    option_symbol  text,
    status         text        NOT NULL DEFAULT 'open'
                               CHECK (status IN ('open', 'filled', 'cancelled', 'expired', 'refused')),
    reason         text,                                      -- why it was refused, expired or cancelled
    triggered      boolean     NOT NULL DEFAULT false,        -- a stop that has crossed its price
    placed_at      timestamptz NOT NULL DEFAULT now(),
    filled_at      timestamptz,
    fill_price     numeric(18, 4),
    trade_id       bigint      REFERENCES trades (id),
    note           text,
    CHECK ((asset = 'stock' AND side IN ('buy', 'sell') AND option_symbol IS NULL)
        OR (asset = 'option' AND side IN ('sell_to_open', 'buy_to_close') AND option_symbol IS NOT NULL)),
    CHECK (type NOT IN ('limit', 'stop_limit') OR limit_price IS NOT NULL),
    CHECK (type NOT IN ('stop', 'stop_limit') OR stop_price IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS orders_status_book ON orders (status, book_id);

-- A sale's proceeds settle the next business day; until then they cannot buy anything (good-faith rule).
CREATE TABLE IF NOT EXISTS settlements (
    trade_id    bigint      PRIMARY KEY REFERENCES trades (id) ON DELETE CASCADE,
    amount      numeric(18, 2) NOT NULL,
    settles_at  timestamptz NOT NULL,
    settled     boolean     NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS settlements_due ON settlements (settled, settles_at);

CREATE TABLE IF NOT EXISTS practice_settings (
    key         text        PRIMARY KEY,
    value       numeric(18, 6) NOT NULL CHECK (value >= 0),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO practice_settings (key, value) VALUES ('fee_per_order', 0) ON CONFLICT (key) DO NOTHING;

-- A snapshot of an option chain as pulled: strikes with bid, ask, volume, open interest, IV and delta.
CREATE TABLE IF NOT EXISTS option_chains (
    option_symbol  text        NOT NULL,
    underlying     text        NOT NULL,
    expiry         date        NOT NULL,
    strike         numeric(18, 4) NOT NULL,
    kind           text        NOT NULL CHECK (kind IN ('put', 'call')),
    bid            numeric(18, 4),
    ask            numeric(18, 4),
    last           numeric(18, 4),
    volume         numeric(20, 2),
    open_interest  numeric(20, 2),
    iv             numeric(12, 6),
    delta          numeric(12, 6),
    source         text        NOT NULL,
    as_of          timestamptz NOT NULL,
    PRIMARY KEY (option_symbol, as_of)
);
CREATE INDEX IF NOT EXISTS option_chains_underlying_expiry ON option_chains (underlying, expiry, as_of DESC);
