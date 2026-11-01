-- Ghostline 0014: ticker changes and mergers (the owner accepted the item, 2026-11-01). Alpaca's name changes, stock
-- mergers, cash mergers and stock-and-cash mergers are applied like splits and dividends (0011): on their own in the
-- practice, AI, options and ghost books, and offered to the owner to confirm for the real book.
--
-- Four new trade sides, all on stock rows, so every reader of trades sees them in time order:
--   convert_out   on the OLD ticker: quantity = the shares held, fill = 0, related_ticker = the NEW ticker. Every open
--                 lot leaves the old ticker. No cash moves.
--   convert_in    on the NEW ticker, written right after its convert_out (same moment, higher id): quantity = the shares
--                 received, fill = 0, related_ticker = the OLD ticker. The lots that left arrive at the same total cost
--                 (each lot's shares × received / held). A name change is 1 for 1; a stock merger uses its rates.
--   cash_out      on the OLD ticker: a cash merger. quantity = the shares taken, fill = the cash paid for all of them
--                 (exact cents, as a dividend's fill). It closes the lots oldest first: realized P&L = cash − cost.
--   cash_in       on the OLD ticker: the cash part of a stock-and-cash merger. quantity = the shares it was paid on,
--                 fill = the cash paid. Realized P&L with no cost: the whole cost stays with the new shares, so the
--                 gain over both parts together is unchanged.

ALTER TABLE trades ADD COLUMN IF NOT EXISTS related_ticker text;

ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_side_check;
ALTER TABLE trades ADD CONSTRAINT trades_side_check
    CHECK (side IN ('buy', 'sell', 'sell_to_open', 'buy_to_close', 'split', 'dividend',
                    'convert_out', 'convert_in', 'cash_out', 'cash_in'));
ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_check;
ALTER TABLE trades ADD CONSTRAINT trades_check
    CHECK ((asset = 'stock' AND side IN ('buy', 'sell', 'split', 'dividend', 'convert_out', 'convert_in', 'cash_out',
                                         'cash_in'))
           OR (asset = 'option'));
-- a conversion always names the other ticker, and nothing else does
ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_related_ticker;
ALTER TABLE trades ADD CONSTRAINT trades_related_ticker
    CHECK ((side IN ('convert_out', 'convert_in')) = (related_ticker IS NOT NULL));

-- a stock position: buys and conversions in, sells, conversions out and cash mergers out, each times every split after
-- it (0011), rounded to the share column
CREATE OR REPLACE VIEW positions AS
SELECT t.book_id, t.ticker,
       ROUND(SUM((CASE WHEN t.side IN ('buy', 'convert_in') THEN t.quantity ELSE -t.quantity END)
                 * COALESCE((SELECT numeric_product(s.quantity) / numeric_product(s.fill) FROM trades s
                             WHERE s.side = 'split' AND s.asset = 'stock' AND s.book_id = t.book_id
                               AND s.ticker = t.ticker AND (s.at, s.id) > (t.at, t.id)), 1)), 6) AS quantity
FROM trades t
WHERE t.asset = 'stock' AND t.side IN ('buy', 'sell', 'convert_in', 'convert_out', 'cash_out')
GROUP BY t.book_id, t.ticker
HAVING ROUND(SUM((CASE WHEN t.side IN ('buy', 'convert_in') THEN t.quantity ELSE -t.quantity END)
                 * COALESCE((SELECT numeric_product(s.quantity) / numeric_product(s.fill) FROM trades s
                             WHERE s.side = 'split' AND s.asset = 'stock' AND s.book_id = t.book_id
                               AND s.ticker = t.ticker AND (s.at, s.id) > (t.at, t.id)), 1)), 6) <> 0;

-- the actions: the new kinds, and the ticker a holder ends up with (a name change's new symbol, a merger's acquirer).
-- For these kinds ex_date is the day the action takes effect (Alpaca's effective_date; a name change's process_date).
-- A stock merger's old_rate / new_rate are Alpaca's acquiree_rate / acquirer_rate (old_rate shares become new_rate);
-- a cash merger's rate is the cash per share, a stock-and-cash merger's rate is its cash_rate.
ALTER TABLE corporate_actions ADD COLUMN IF NOT EXISTS new_ticker text;
ALTER TABLE corporate_actions DROP CONSTRAINT IF EXISTS corporate_actions_kind_check;
ALTER TABLE corporate_actions ADD CONSTRAINT corporate_actions_kind_check
    CHECK (kind IN ('forward_split', 'reverse_split', 'cash_dividend',
                    'name_change', 'stock_merger', 'cash_merger', 'stock_and_cash_merger'));
