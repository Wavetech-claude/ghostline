-- Automatic recurring buys in practice (the owner's go, 2026-11-01): "buy $X of TICKER every day, week, two weeks or
-- month", fractional, placed by the worker on trading days only. Each run is an ordinary practice market order, so
-- history, statements, edit-void and the risk rules treat it like any other. A scheduled day when the market is
-- closed moves to the next session. Practice book only: the AI book trades its own ideas, and its S&P 500 ghost
-- mirrors deposits, not purchases, so a schedule there would blur the test of the AI against the ghost.
CREATE TABLE IF NOT EXISTS recurring_plans (
    id          bigserial   PRIMARY KEY,
    book        text        NOT NULL DEFAULT 'practice' CHECK (book = 'practice'),
    ticker      text        NOT NULL,
    dollars     numeric(18, 2) NOT NULL CHECK (dollars > 0),
    every       text        NOT NULL CHECK (every IN ('day', 'week', 'two_weeks', 'month')),
    weekday     smallint    CHECK (weekday BETWEEN 0 AND 4),            -- Monday 0 … Friday 4
    month_day   smallint    CHECK (month_day BETWEEN 1 AND 31),         -- a short month runs on its last day
    starts_on   date        NOT NULL,
    status      text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'deleted')),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CHECK ((every IN ('week', 'two_weeks')) = (weekday IS NOT NULL)),
    CHECK ((every = 'month') = (month_day IS NOT NULL))
);

-- one row per plan per session it was due: placed (with its order) or skipped (with the reason)
CREATE TABLE IF NOT EXISTS recurring_runs (
    id          bigserial   PRIMARY KEY,
    plan_id     bigint      NOT NULL REFERENCES recurring_plans (id),
    day         date        NOT NULL,
    scheduled   date        NOT NULL,                                   -- the plan's own date (a holiday moves it)
    status      text        NOT NULL CHECK (status IN ('placed', 'skipped')),
    order_id    bigint      REFERENCES orders (id),
    reason      text,
    at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (plan_id, day)
);
