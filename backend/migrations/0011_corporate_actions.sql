-- Ghostline 0011: corporate actions (the owner kept the proposed item, 2026-10-30). Splits and cash dividends from
-- Alpaca are applied to the practice, AI, options and ghost books, and offered to the owner to confirm for the real book.
--
-- Two new trade sides carry them in the ledger, so every reader of trades sees them in time order:
--   split     quantity = the ratio's new count, fill = its old count (4-for-1: 4 and 1; 1-for-3: 1 and 3); every open lot's
--             shares become shares × new / old at the same total cost. No cash moves.
--   dividend  quantity = 1, fill = the cash paid (exact cents: a rate can carry more decimals than the fill column);
--             the cash comes in as return, not as a deposit. The rate and the shares are in corporate_action_books.

ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_side_check;
ALTER TABLE trades ADD CONSTRAINT trades_side_check
    CHECK (side IN ('buy', 'sell', 'sell_to_open', 'buy_to_close', 'split', 'dividend'));
-- a stock row is a buy, a sell, or one of the two corporate actions (0002's row check allowed buy and sell only)
ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_check;
ALTER TABLE trades ADD CONSTRAINT trades_check
    CHECK ((asset = 'stock' AND side IN ('buy', 'sell', 'split', 'dividend')) OR (asset = 'option'));

-- the exact product of a column (numeric multiplication, no floating point): splits compound
CREATE OR REPLACE AGGREGATE numeric_product(numeric) (SFUNC = numeric_mul, STYPE = numeric);

-- a stock position: each buy and sell, times every split after it (new over old, exact), rounded to the share column
CREATE OR REPLACE VIEW positions AS
SELECT t.book_id, t.ticker,
       ROUND(SUM((CASE WHEN t.side = 'buy' THEN t.quantity ELSE -t.quantity END)
                 * COALESCE((SELECT numeric_product(s.quantity) / numeric_product(s.fill) FROM trades s
                             WHERE s.side = 'split' AND s.asset = 'stock' AND s.book_id = t.book_id
                               AND s.ticker = t.ticker AND (s.at, s.id) > (t.at, t.id)), 1)), 6) AS quantity
FROM trades t
WHERE t.asset = 'stock' AND t.side IN ('buy', 'sell')
GROUP BY t.book_id, t.ticker
HAVING ROUND(SUM((CASE WHEN t.side = 'buy' THEN t.quantity ELSE -t.quantity END)
                 * COALESCE((SELECT numeric_product(s.quantity) / numeric_product(s.fill) FROM trades s
                             WHERE s.side = 'split' AND s.asset = 'stock' AND s.book_id = t.book_id
                               AND s.ticker = t.ticker AND (s.at, s.id) > (t.at, t.id)), 1)), 6) <> 0;

CREATE TABLE IF NOT EXISTS corporate_actions (
    id            text        PRIMARY KEY,              -- Alpaca's id
    kind          text        NOT NULL CHECK (kind IN ('forward_split', 'reverse_split', 'cash_dividend')),
    ticker        text        NOT NULL,
    ex_date       date        NOT NULL,
    record_date   date,
    payable_date  date,
    old_rate      numeric(18, 6),                        -- splits
    new_rate      numeric(18, 6),
    rate          numeric(18, 6),                        -- a cash dividend, per share
    source        text        NOT NULL,
    as_of         timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS corporate_actions_ticker ON corporate_actions (ticker, ex_date);

-- what happened to each action in each book that held the stock: applied (a simulated book), pending (the real book,
-- waiting for the owner), confirmed (the owner said the broker shows it), dismissed (the owner said it does not)
CREATE TABLE IF NOT EXISTS corporate_action_books (
    action_id   text        NOT NULL REFERENCES corporate_actions (id),
    book_id     integer     NOT NULL REFERENCES books (id),
    status      text        NOT NULL CHECK (status IN ('applied', 'pending', 'confirmed', 'dismissed')),
    shares      numeric(18, 6) NOT NULL,                -- held before the split, or held for the dividend
    amount      numeric(18, 2),                         -- a dividend's cash
    trade_id    bigint      REFERENCES trades (id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    decided_at  timestamptz,
    PRIMARY KEY (action_id, book_id)
);
