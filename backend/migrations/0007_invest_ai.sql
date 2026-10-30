-- Ghostline 0007: "Invest this", the AI trader and its S&P 500 ghost, options practice on any amount, the to-do list
-- for real money, price alerts, and the lanes' graduation (filled by the learning loop in phase 7; every lane starts
-- not graduated, so "copy with real money" is refused until then).

-- options practice runs on its own book, funded with any amount
ALTER TABLE books DROP CONSTRAINT IF EXISTS books_kind_check;
ALTER TABLE books ADD CONSTRAINT books_kind_check CHECK (kind IN ('real', 'practice', 'ai', 'ghost', 'options'));
INSERT INTO books (kind, name) VALUES ('options', 'Options practice') ON CONFLICT (kind) DO NOTHING;

-- an order and its trade remember the idea and the lane they came from (the AI trader's reasons, the journal)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS idea_id bigint REFERENCES ideas (id);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS lane text CHECK (lane IS NULL OR lane IN ('long', 'swing', 'puts', 'day'));
CREATE INDEX IF NOT EXISTS orders_idea ON orders (idea_id) WHERE idea_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS trades_idea ON trades (idea_id) WHERE idea_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS invest_plans (
    id          bigserial   PRIMARY KEY,
    amount      numeric(18, 2) NOT NULL CHECK (amount > 0),
    for_date    date        NOT NULL,
    book        text        NOT NULL CHECK (book IN ('practice', 'real')),
    splits      jsonb       NOT NULL,                   -- [{idea_id, ticker, lane, dollars, price, shares, stop, decision, reason}]
    unallocated numeric(18, 2) NOT NULL DEFAULT 0,
    status      text        NOT NULL DEFAULT 'proposed'
                            CHECK (status IN ('proposed', 'approved', 'placed', 'dropped')),
    approved_to text        CHECK (approved_to IS NULL OR approved_to IN ('practice', 'real')),
    result      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    decided_at  timestamptz,
    placed_at   timestamptz
);
CREATE INDEX IF NOT EXISTS invest_plans_status_date ON invest_plans (status, for_date);

-- real money is placed by the owner at the broker: the app keeps a to-do item, "I placed it" pre-fills the form
CREATE TABLE IF NOT EXISTS todos (
    id           bigserial   PRIMARY KEY,
    source       text        NOT NULL CHECK (source IN ('invest', 'ai_copy')),
    source_id    bigint,
    ticker       text        NOT NULL,
    asset        text        NOT NULL DEFAULT 'stock' CHECK (asset IN ('stock', 'option')),
    side         text        NOT NULL CHECK (side IN ('buy', 'sell', 'sell_to_open', 'buy_to_close')),
    quantity     numeric(18, 6) NOT NULL CHECK (quantity > 0),
    limit_price  numeric(18, 4),
    option_symbol text,
    lane         text,
    idea_id      bigint      REFERENCES ideas (id),
    note         text,
    status       text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'dropped')),
    trade_id     bigint      REFERENCES trades (id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    closed_at    timestamptz
);
CREATE INDEX IF NOT EXISTS todos_open ON todos (status, created_at DESC);

-- the ghost mirrors every AI-book cash flow with an SPY trade at that day's close
CREATE TABLE IF NOT EXISTS ghost_links (
    ai_flow_id     bigint      PRIMARY KEY REFERENCES cash_flows (id),
    ghost_flow_id  bigint      NOT NULL REFERENCES cash_flows (id),
    trade_id       bigint      REFERENCES trades (id),
    price          numeric(18, 4),
    price_source   text,
    price_date     date,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_trader_runs (
    id          bigserial   PRIMARY KEY,
    kind        text        NOT NULL CHECK (kind IN ('entries', 'exits', 'options')),
    result      jsonb       NOT NULL,
    at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lane_graduation (
    lane                text        PRIMARY KEY CHECK (lane IN ('long', 'swing', 'puts')),
    settled_n           integer     NOT NULL DEFAULT 0,
    expectancy          numeric(12, 6),
    positive_expectancy boolean     NOT NULL DEFAULT false,
    graduated           boolean     NOT NULL DEFAULT false,
    updated_at          timestamptz NOT NULL DEFAULT now()
);
INSERT INTO lane_graduation (lane) VALUES ('long'), ('swing'), ('puts') ON CONFLICT (lane) DO NOTHING;

CREATE TABLE IF NOT EXISTS alerts (
    id            bigserial   PRIMARY KEY,
    ticker        text        NOT NULL,
    kind          text        NOT NULL CHECK (kind IN ('above', 'below', 'pct_move', 'ma_cross', 'earnings')),
    value         numeric(18, 4) NOT NULL CHECK (value > 0),  -- a price, a percent, an MA period, or days
    status        text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'triggered', 'off')),
    note          text,
    message       text,                                       -- what it said when it fired
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_checked  timestamptz,
    triggered_at  timestamptz
);
CREATE INDEX IF NOT EXISTS alerts_active ON alerts (status, ticker);
