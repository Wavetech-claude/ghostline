-- Brand names that the company's name lacks (the owner, 2026-11-02: "google" found nothing, because the company is
-- "Alphabet Inc."). Search matches an alias by its prefix, as it matches a word of the name, so "face" finds Meta.
-- Lower case. A ticker the directory does not list is never shown: search reads the directory and joins this table.
CREATE TABLE IF NOT EXISTS ticker_aliases (
    alias   text NOT NULL CHECK (alias = lower(alias) AND alias <> ''),
    ticker  text NOT NULL CHECK (ticker = upper(ticker)),
    PRIMARY KEY (alias, ticker)
);

INSERT INTO ticker_aliases (alias, ticker) VALUES
    ('google', 'GOOGL'), ('google', 'GOOG'), ('youtube', 'GOOGL'), ('youtube', 'GOOG'),
    ('facebook', 'META'), ('instagram', 'META'), ('whatsapp', 'META'),
    ('snapchat', 'SNAP'), ('coke', 'KO'), ('tsmc', 'TSM'), ('venmo', 'PYPL'), ('bofa', 'BAC')
ON CONFLICT DO NOTHING;
