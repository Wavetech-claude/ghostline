-- Ghostline 0009: the exchange calendar (Alpaca's), pulled daily about a year ahead. A day in the pulled range with
-- no row is a closed day (a holiday); early closes and settlement dates come with each session.

CREATE TABLE IF NOT EXISTS market_sessions (
    day         date        PRIMARY KEY,
    opens_at    timestamptz NOT NULL,
    closes_at   timestamptz NOT NULL,
    settles_on  date        NOT NULL,                  -- when a sale made this day settles (T+1, the bank calendar)
    source      text        NOT NULL,
    as_of       timestamptz NOT NULL
);
