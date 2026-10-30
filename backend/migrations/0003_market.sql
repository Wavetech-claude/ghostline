-- Ghostline 0003: market data (cached pulls, every row with its source and as-of time) and the watchlist.
-- Nothing published after an analysis date is fed back in: readers filter on as_of (phase 4).

CREATE TABLE IF NOT EXISTS watchlist (
    ticker      text        PRIMARY KEY,
    note        text,
    added_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_bars (
    ticker     text        NOT NULL,
    timeframe  text        NOT NULL,            -- 1D | 1W | 1M (bar length)
    ts         timestamptz NOT NULL,
    open       numeric(18, 4) NOT NULL,
    high       numeric(18, 4) NOT NULL,
    low        numeric(18, 4) NOT NULL,
    close      numeric(18, 4) NOT NULL,
    volume     numeric(20, 2) NOT NULL DEFAULT 0,
    source     text        NOT NULL,
    as_of      timestamptz NOT NULL,
    PRIMARY KEY (ticker, timeframe, ts)
);

CREATE TABLE IF NOT EXISTS quotes (
    id       bigserial   PRIMARY KEY,
    ticker   text        NOT NULL,
    bid      numeric(18, 4),
    ask      numeric(18, 4),
    last     numeric(18, 4),
    ts       timestamptz NOT NULL,              -- the quote's own time
    source   text        NOT NULL,
    as_of    timestamptz NOT NULL,              -- when it was pulled
    delayed  boolean     NOT NULL DEFAULT true  -- free data is delayed or IEX-only; the app says so
);
CREATE INDEX IF NOT EXISTS quotes_ticker_as_of ON quotes (ticker, as_of DESC);

CREATE TABLE IF NOT EXISTS fundamentals (
    ticker   text        NOT NULL,
    as_of    timestamptz NOT NULL,
    source   text        NOT NULL,
    payload  jsonb       NOT NULL,
    PRIMARY KEY (ticker, source, as_of)
);

CREATE TABLE IF NOT EXISTS filings (
    accession  text        PRIMARY KEY,
    ticker     text        NOT NULL,
    form       text        NOT NULL,
    filed_at   date        NOT NULL,
    url        text,
    source     text        NOT NULL,
    as_of      timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS filings_ticker ON filings (ticker, filed_at DESC);

CREATE TABLE IF NOT EXISTS news (
    id            bigserial   PRIMARY KEY,
    ticker        text,
    headline      text        NOT NULL,
    url           text        NOT NULL,
    published_at  timestamptz NOT NULL,
    source        text        NOT NULL,
    as_of         timestamptz NOT NULL,
    tone          numeric(8, 3),
    UNIQUE (ticker, url)
);
CREATE INDEX IF NOT EXISTS news_ticker_published ON news (ticker, published_at DESC);

CREATE TABLE IF NOT EXISTS earnings (
    ticker     text        NOT NULL,
    at         date        NOT NULL,
    estimate   numeric(18, 4),
    actual     numeric(18, 4),
    source     text        NOT NULL,
    as_of      timestamptz NOT NULL,
    PRIMARY KEY (ticker, at)
);

CREATE TABLE IF NOT EXISTS macro_series (
    series  text        NOT NULL,
    ts      date        NOT NULL,
    value   numeric(18, 6),
    source  text        NOT NULL,
    as_of   timestamptz NOT NULL,
    PRIMARY KEY (series, ts)
);

-- Every call to a source: the cache's memory and the free-tier counters (per minute, per day).
CREATE TABLE IF NOT EXISTS data_pulls (
    id        bigserial   PRIMARY KEY,
    source    text        NOT NULL,
    endpoint  text        NOT NULL,
    status    text        NOT NULL,              -- ok | error | skipped
    detail    text,
    rows      integer     NOT NULL DEFAULT 0,
    at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS data_pulls_source_at ON data_pulls (source, at DESC);
