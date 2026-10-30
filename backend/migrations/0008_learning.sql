-- Ghostline 0008: the journal and the learning loop. Every idea is paper-traded at the next open and settled at the
-- next open after its horizon, against SPY over the same days, after estimated costs. Modules' weights follow their
-- expectancy (pulled toward equal until they have enough trades); a module or a lane graduates only from settled
-- results. Claude writes a lesson per settled idea; a weekly test set with known answers is scored; a weekly digest.

CREATE TABLE IF NOT EXISTS learning_settings (
    key         text        PRIMARY KEY,
    value       numeric(18, 6) NOT NULL CHECK (value >= 0),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO learning_settings (key, value) VALUES
    ('graduation_n', 30),          -- settled paper trades a lane or module needs before it can graduate
    ('cost_bps_per_side', 10),     -- estimated cost of each fill (spread and slippage), basis points
    ('weight_full_n', 20)          -- settled trades before a module's weight moves fully with its expectancy
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS paper_results (
    idea_id      bigint      PRIMARY KEY REFERENCES ideas (id),
    ticker       text        NOT NULL,
    lane         text        NOT NULL,
    module       text        NOT NULL,
    opened_on    date        NOT NULL,                 -- the first session after the idea: its "next open"
    closes_on    date        NOT NULL,                 -- opened_on + the idea's horizon; settled at the next open
    status       text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'settled', 'unpriced')),
    entry        numeric(18, 4),
    exit         numeric(18, 4),
    entry_date   date,
    exit_date    date,
    result_pct   numeric(12, 4),                       -- after estimated costs
    spy_entry    numeric(18, 4),
    spy_exit     numeric(18, 4),
    spy_pct      numeric(12, 4),
    costs_pct    numeric(12, 4),
    excess_pct   numeric(12, 4),                       -- result minus SPY over the same days
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    settled_at   timestamptz
);
CREATE INDEX IF NOT EXISTS paper_results_status ON paper_results (status, closes_on);

CREATE TABLE IF NOT EXISTS module_weights (
    module       text        PRIMARY KEY,
    n            integer     NOT NULL DEFAULT 0,
    wins         integer     NOT NULL DEFAULT 0,
    expectancy   numeric(12, 4),                       -- mean excess return per settled idea, percent
    weight       numeric(8, 4) NOT NULL DEFAULT 1,
    graduated    boolean     NOT NULL DEFAULT false,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lessons (
    id          bigserial   PRIMARY KEY,
    idea_id     bigint      NOT NULL UNIQUE REFERENCES ideas (id),
    ticker      text        NOT NULL,
    module      text        NOT NULL,
    text        text        NOT NULL,
    grounded    boolean     NOT NULL DEFAULT false,
    trace_id    text,
    job_id      bigint      REFERENCES claude_jobs (id),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id          bigserial   PRIMARY KEY,
    questions   integer     NOT NULL,
    correct     integer     NOT NULL,
    score_pct   numeric(6, 2) NOT NULL,
    results     jsonb       NOT NULL,                  -- [{question, expected, answer, correct}]
    job_id      bigint      REFERENCES claude_jobs (id),
    at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS weekly_digests (
    id          bigserial   PRIMARY KEY,
    week_of     date        NOT NULL UNIQUE,           -- the Monday of the week it covers
    content     text        NOT NULL,
    facts       jsonb       NOT NULL,
    pushed_at   timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Claude's two new jobs: a lesson per settled idea, and the weekly test set
ALTER TABLE claude_jobs DROP CONSTRAINT IF EXISTS claude_jobs_kind_check;
ALTER TABLE claude_jobs ADD CONSTRAINT claude_jobs_kind_check
    CHECK (kind IN ('deep_dive', 'chat', 'brief', 'read_attachment', 'lesson', 'eval'));
