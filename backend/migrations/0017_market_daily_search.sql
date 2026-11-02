-- Search as you type, Robinhood style (the owner, 2026-11-01): typing "n" shows the popular N stocks (Nvidia, Netflix,
-- Nike), and a name finds a stock whose ticker you do not know.

-- The whole market's daily close and volume from Massive's grouped daily file (one call a session, ~12,600 tickers).
-- Search ranks by 20-day average dollar volume from it; discovery's weekly and monthly movers read the same table.
-- Kept for 100 calendar days (about 70 sessions, under a million rows), pruned by the pull.
CREATE TABLE IF NOT EXISTS market_daily (
    day     date           NOT NULL,
    ticker  text           NOT NULL,
    open    numeric(18, 4),
    close   numeric(18, 4) NOT NULL,
    volume  numeric(20, 0) NOT NULL DEFAULT 0,
    source  text           NOT NULL,
    as_of   timestamptz    NOT NULL,
    PRIMARY KEY (day, ticker)
);
CREATE INDEX IF NOT EXISTS market_daily_ticker ON market_daily (ticker, day DESC);

-- What a ticker is (Massive's type: CS common stock, ETF, ADRC, PFD, WARRANT, RIGHT, UNIT, ...) and how much of it
-- trades: the 20-day average of close x volume, refreshed after each daily pull.
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS kind text;
ALTER TABLE tickers ADD COLUMN IF NOT EXISTS dollar_volume_20d numeric(22, 2);
-- A ticker prefix (ticker LIKE 'NV%') uses this index; tickers are stored upper case, so no ILIKE is needed. A name's
-- word prefix is a regular expression over about 13,000 names: a sequential scan, measured in a few milliseconds.
CREATE INDEX IF NOT EXISTS tickers_prefix ON tickers (ticker text_pattern_ops);
