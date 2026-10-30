-- Ghostline 0005: ideas and modules. Every module writes the same signal record; ideas are signals that passed (or
-- were refused by) the risk rules, in one of three lanes, sized to the balance; memos arrive in phase 5.

CREATE TABLE IF NOT EXISTS scan_universe (
    ticker         text    NOT NULL,
    day            date    NOT NULL,
    reason         text    NOT NULL,                 -- movers | tracked | market
    dollar_volume  numeric(24, 2),
    change_pct     numeric(10, 4),
    PRIMARY KEY (ticker, day)
);

CREATE TABLE IF NOT EXISTS signals (
    id            bigserial   PRIMARY KEY,
    ticker        text        NOT NULL,
    module        text        NOT NULL,                -- vcp | canslim | pead | earnings_trade | macro_regime | sell_puts
    lane          text        NOT NULL CHECK (lane IN ('long', 'swing', 'puts', 'macro')),
    direction     text        NOT NULL CHECK (direction IN ('long', 'short', 'short_put', 'neutral')),
    score         numeric(6, 2) NOT NULL CHECK (score >= 0 AND score <= 100),
    actionable    boolean     NOT NULL DEFAULT false,  -- inside the module's entry window right now
    horizon_days  integer     NOT NULL,
    entry         numeric(18, 4),
    stop          numeric(18, 4),
    target        numeric(18, 4),
    reason        text        NOT NULL,                -- one plain sentence: why this, why now (or why not)
    evidence      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    day           date        NOT NULL,                -- the scan's day; one row per ticker, module and day
    source        text,                                -- the bars' source
    as_of         timestamptz NOT NULL,                -- the newest stored row the module read
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticker, module, day)
);
CREATE INDEX IF NOT EXISTS signals_day_lane ON signals (day DESC, lane);

CREATE TABLE IF NOT EXISTS ideas (
    id                bigserial   PRIMARY KEY,
    lane              text        NOT NULL CHECK (lane IN ('long', 'swing', 'puts')),
    ticker            text        NOT NULL,
    signal_id         bigint      NOT NULL REFERENCES signals (id),
    module            text        NOT NULL,
    status            text        NOT NULL DEFAULT 'proposed'
                                  CHECK (status IN ('proposed', 'open', 'refused', 'expired', 'settled')),
    book              text        NOT NULL,            -- the book the sizing read: real | practice
    balance           numeric(18, 2) NOT NULL,         -- that book's equity at the scan
    entry             numeric(18, 4),
    stop              numeric(18, 4),
    target            numeric(18, 4),
    quantity          numeric(18, 6),                  -- shares (fractions) or contracts
    size_share        numeric(8, 6),                   -- position value as a share of the balance
    max_loss          numeric(18, 2),                  -- entry to stop, in money
    collateral        numeric(18, 2),                  -- puts: strike × 100 × contracts
    option_symbol     text,
    risk_decision_id  bigint      REFERENCES risk_decisions (id),
    refusal           text,                            -- the rule and reason when refused
    opens_at          timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ideas_lane_status ON ideas (lane, status, created_at DESC);

CREATE TABLE IF NOT EXISTS memos (
    id               bigserial   PRIMARY KEY,
    idea_id          bigint      REFERENCES ideas (id),
    ticker           text        NOT NULL,
    call             text        NOT NULL CHECK (call IN ('buy', 'wait', 'avoid')),
    size_money       numeric(18, 2),
    bull             text,
    base             text,
    bear             text,
    catalysts        text,
    invalidation     text,
    confidence_low   numeric(5, 2),
    confidence_high  numeric(5, 2),
    grounded         boolean     NOT NULL DEFAULT false,
    trace_id         text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
