-- Ghostline 0012: the ticker directory behind "search any stock" (the owner, 2026-11-01: like Robinhood). The SEC's
-- company list (www.sec.gov/files/company_tickers.json: ticker, company name, CIK; free, no key) refreshed daily in
-- production; staging reads production's copy. The CIK is also the key to EDGAR's company facts.

CREATE TABLE IF NOT EXISTS tickers (
    ticker text PRIMARY KEY,
    name   text NOT NULL,
    cik    text,
    source text NOT NULL,
    as_of  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS tickers_name ON tickers (lower(name));
