-- Ghostline 0015: edit, void and reset (the owner approved, 2026-11-01).
--
-- Nothing in the ledger is ever deleted. A wrong entry is VOIDED: it stays, with when and why, and every number
-- (positions, P&L, cash, statements, the value graph, the risk checks) leaves it out. Unvoid brings it back.
--   * the real book: the owner edits any field of a trade or a cash flow, or voids one; each change is a row in
--     ledger_edits (an edit writes one row per field, a void or unvoid one row);
--   * the practice, AI and options books: fills are never edited (a broker's paper account does not either); a RESET
--     voids every entry of the book at once with the reason "reset YYYY-MM-DD", cancels its open orders, and for the
--     AI book voids the S&P 500 ghost's entries too (the ghost only mirrors the AI book's deposits).
--
-- Readers never touch trades or cash_flows directly: they read live_trades and live_cash_flows, the rows not voided.
-- A test (test_voided_rows_guard) fails when backend code reads the base tables outside the few places that must see
-- voided rows. ⚠ A view's column list is fixed when it is made: a migration that adds a column to trades or
-- cash_flows must re-create the live view below (test_live_views_have_every_column checks it).

ALTER TABLE trades ADD COLUMN IF NOT EXISTS voided_at timestamptz;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS void_reason text;
ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_void_reason;
ALTER TABLE trades ADD CONSTRAINT trades_void_reason CHECK ((voided_at IS NULL) = (void_reason IS NULL));

ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS voided_at timestamptz;
ALTER TABLE cash_flows ADD COLUMN IF NOT EXISTS void_reason text;
ALTER TABLE cash_flows DROP CONSTRAINT IF EXISTS cash_flows_void_reason;
ALTER TABLE cash_flows ADD CONSTRAINT cash_flows_void_reason CHECK ((voided_at IS NULL) = (void_reason IS NULL));

CREATE OR REPLACE VIEW live_trades AS SELECT * FROM trades WHERE voided_at IS NULL;
CREATE OR REPLACE VIEW live_cash_flows AS SELECT * FROM cash_flows WHERE voided_at IS NULL;

-- every edit, void and unvoid, oldest first per entry
CREATE TABLE IF NOT EXISTS ledger_edits (
    id          bigserial   PRIMARY KEY,
    entity      text        NOT NULL CHECK (entity IN ('trade', 'cash_flow')),
    entity_id   bigint      NOT NULL,
    action      text        NOT NULL CHECK (action IN ('edit', 'void', 'unvoid')),
    field       text,                               -- an edit: the field changed
    old         text,                               -- the value before, as text
    new         text,                               -- the value after, as text
    reason      text,                               -- a void: why
    by_whom     text        NOT NULL DEFAULT 'owner',
    at          timestamptz NOT NULL DEFAULT now(),
    CHECK ((action = 'edit') = (field IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ledger_edits_entity ON ledger_edits (entity, entity_id, id);

-- a practice order replaced by the owner: the old one ends 'replaced' and the new one names it
ALTER TABLE orders ADD COLUMN IF NOT EXISTS replaces_order_id bigint REFERENCES orders (id);
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check
    CHECK (status IN ('open', 'filled', 'cancelled', 'expired', 'refused', 'replaced'));

-- the positions view (0014) over the live rows only
CREATE OR REPLACE VIEW positions AS
SELECT t.book_id, t.ticker,
       ROUND(SUM((CASE WHEN t.side IN ('buy', 'convert_in') THEN t.quantity ELSE -t.quantity END)
                 * COALESCE((SELECT numeric_product(s.quantity) / numeric_product(s.fill) FROM live_trades s
                             WHERE s.side = 'split' AND s.asset = 'stock' AND s.book_id = t.book_id
                               AND s.ticker = t.ticker AND (s.at, s.id) > (t.at, t.id)), 1)), 6) AS quantity
FROM live_trades t
WHERE t.asset = 'stock' AND t.side IN ('buy', 'sell', 'convert_in', 'convert_out', 'cash_out')
GROUP BY t.book_id, t.ticker
HAVING ROUND(SUM((CASE WHEN t.side IN ('buy', 'convert_in') THEN t.quantity ELSE -t.quantity END)
                 * COALESCE((SELECT numeric_product(s.quantity) / numeric_product(s.fill) FROM live_trades s
                             WHERE s.side = 'split' AND s.asset = 'stock' AND s.book_id = t.book_id
                               AND s.ticker = t.ticker AND (s.at, s.id) > (t.at, t.id)), 1)), 6) <> 0;
