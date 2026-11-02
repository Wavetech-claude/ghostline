-- Practice order types (the owner's go, 2026-11-01): a trailing stop and a bracket.
--
-- A trailing stop (stocks) follows the best price since it was placed: for a sale the highest bid seen (water_mark),
-- for a buy the lowest ask; its stop_price is that mark less the trail (an amount or a percent), moved only in the
-- order's favour, and it fills as a stop once the market crosses it. The mark moves on the quotes the practice
-- engine already reads (every 30 minutes in the session, and at placement).
--
-- A bracket is a stock buy carrying take_profit and bracket_stop. When the buy fills, two sell orders for the same
-- shares open, each naming it in parent_order_id: a limit at take_profit and a stop at bracket_stop, both good till
-- cancelled. One filling or being cancelled cancels the other (one-cancels-other).
ALTER TABLE orders ADD COLUMN IF NOT EXISTS trail_amount numeric(18, 4) CHECK (trail_amount IS NULL OR trail_amount > 0);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS trail_percent numeric(9, 4)
    CHECK (trail_percent IS NULL OR (trail_percent > 0 AND trail_percent < 100));
ALTER TABLE orders ADD COLUMN IF NOT EXISTS water_mark numeric(18, 4);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS take_profit numeric(18, 4) CHECK (take_profit IS NULL OR take_profit > 0);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS bracket_stop numeric(18, 4) CHECK (bracket_stop IS NULL OR bracket_stop > 0);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS parent_order_id bigint REFERENCES orders (id);
CREATE INDEX IF NOT EXISTS orders_parent ON orders (parent_order_id) WHERE parent_order_id IS NOT NULL;

ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_type_check;
ALTER TABLE orders ADD CONSTRAINT orders_type_check
    CHECK (type IN ('market', 'limit', 'stop', 'stop_limit', 'trailing_stop'));

-- a trailing stop is a stock order with exactly one trail; nothing else carries a trail
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_trailing_check;
ALTER TABLE orders ADD CONSTRAINT orders_trailing_check
    CHECK ((type = 'trailing_stop' AND asset = 'stock' AND (trail_amount IS NULL) <> (trail_percent IS NULL))
        OR (type <> 'trailing_stop' AND trail_amount IS NULL AND trail_percent IS NULL));

-- a bracket is a stock buy with both legs, the target above the stop
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_bracket_check;
ALTER TABLE orders ADD CONSTRAINT orders_bracket_check
    CHECK ((take_profit IS NULL AND bracket_stop IS NULL)
        OR (take_profit IS NOT NULL AND bracket_stop IS NOT NULL AND take_profit > bracket_stop
            AND asset = 'stock' AND side = 'buy'));
