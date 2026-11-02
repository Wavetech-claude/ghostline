-- The earnings calendar and analysts' views (the owner, 2026-11-01).
-- Finnhub's earnings calendar also says when in the day a company reports (before the open, after the close, during
-- the session) and its revenue estimate; both were dropped until now.
ALTER TABLE earnings ADD COLUMN IF NOT EXISTS hour text CHECK (hour IN ('bmo', 'amc', 'dmh'));
ALTER TABLE earnings ADD COLUMN IF NOT EXISTS revenue_estimate numeric(22, 2);
ALTER TABLE earnings ADD COLUMN IF NOT EXISTS revenue_actual numeric(22, 2);

-- Finnhub's recommendation trends: how many analysts say strong buy, buy, hold, sell, strong sell, per month.
CREATE TABLE IF NOT EXISTS analyst_trends (
    ticker       text        NOT NULL,
    period       date        NOT NULL,
    strong_buy   integer     NOT NULL DEFAULT 0,
    buy          integer     NOT NULL DEFAULT 0,
    hold         integer     NOT NULL DEFAULT 0,
    sell         integer     NOT NULL DEFAULT 0,
    strong_sell  integer     NOT NULL DEFAULT 0,
    source       text        NOT NULL,
    as_of        timestamptz NOT NULL,
    PRIMARY KEY (ticker, period)
);

-- FMP's consensus of analysts' grades and their price targets, one row per stock.
CREATE TABLE IF NOT EXISTS analyst_consensus (
    ticker            text        PRIMARY KEY,
    strong_buy        integer,
    buy               integer,
    hold              integer,
    sell              integer,
    strong_sell       integer,
    consensus         text,
    target_high       numeric(18, 4),
    target_low        numeric(18, 4),
    target_consensus  numeric(18, 4),
    target_median     numeric(18, 4),
    source            text        NOT NULL,
    as_of             timestamptz NOT NULL
);

-- When each source was last asked about a stock, answered or not, so an empty answer is not asked again every day.
CREATE TABLE IF NOT EXISTS analyst_pulls (
    ticker  text        NOT NULL,
    source  text        NOT NULL,
    at      timestamptz NOT NULL DEFAULT now(),
    rows    integer     NOT NULL DEFAULT 0,
    PRIMARY KEY (ticker, source)
);
