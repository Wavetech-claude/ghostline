-- Ghostline 0013: reported EPS and revenue from SEC EDGAR for every scanned stock (the owner, 2026-11-01: the long-term
-- lane was empty because FMP's free plan covers only what the owner holds or watches). Company concepts by CIK
-- (data.sec.gov/api/xbrl/companyconcept, free, no key): 3-month quarters from 10-Qs, years from 10-Ks; the later filing
-- wins when a period is restated. CANSLIM reads it only when FMP has nothing for the stock.

CREATE TABLE IF NOT EXISTS eps_history (
    ticker     text          NOT NULL,
    kind       text          NOT NULL CHECK (kind IN ('quarter', 'year')),
    period_end date          NOT NULL,
    eps        numeric(14, 4) NOT NULL,
    revenue    numeric(24, 2),
    source     text          NOT NULL,
    as_of      timestamptz   NOT NULL,
    PRIMARY KEY (ticker, kind, period_end)
);
