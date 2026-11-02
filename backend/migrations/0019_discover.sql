-- Discover (the owner, 2026-11-01): top gainers, losers and most active by day, week and month, sliced by stocks or
-- ETFs and by industry; sectors, industries and themes.

-- Alpaca's live movers and most active (the whole market, top 50 each), replaced on every pull during the session.
CREATE TABLE IF NOT EXISTS market_movers (
    kind         text           NOT NULL CHECK (kind IN ('gainers', 'losers', 'active')),
    rank         integer        NOT NULL,
    ticker       text           NOT NULL,
    price        numeric(18, 4),
    change       numeric(18, 4),
    change_pct   numeric(12, 4),
    volume       numeric(20, 0),
    trade_count  bigint,
    source       text           NOT NULL,
    as_of        timestamptz    NOT NULL,
    PRIMARY KEY (kind, rank)
);

-- Each traded stock's industry (Finnhub's company profile: Technology, Semiconductors, Banking, Construction, ...),
-- refreshed weekly; industry_at also marks a stock asked about that had none, so it is not asked every day.
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS industry text;
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS industry_at timestamptz;
CREATE INDEX IF NOT EXISTS tickers_industry ON tickers (industry) WHERE industry IS NOT NULL;
