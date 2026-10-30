-- Ghostline 0006: Claude in the worker. A job queue the api fills and the worker drains one at a time, the chat
-- thread, the morning brief, and the memos' trace ids. Every job records what it read and what it wrote.

CREATE TABLE IF NOT EXISTS claude_jobs (
    id           bigserial   PRIMARY KEY,
    kind         text        NOT NULL CHECK (kind IN ('deep_dive', 'chat', 'brief', 'read_attachment')),
    status       text        NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    payload      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    result       jsonb,
    error        text,
    trace_id     text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz
);
CREATE INDEX IF NOT EXISTS claude_jobs_queue ON claude_jobs (status, id);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          bigserial   PRIMARY KEY,
    ticker      text,                                   -- the thread's ticker, when the talk is about one
    role        text        NOT NULL CHECK (role IN ('owner', 'ghostline')),
    content     text        NOT NULL,
    job_id      bigint      REFERENCES claude_jobs (id),
    grounded    boolean,                                -- ghostline's answers: every number matched a stored value
    struck      jsonb,                                  -- the numbers that did not
    trace_id    text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_ticker_at ON chat_messages (ticker, created_at);

CREATE TABLE IF NOT EXISTS briefs (
    id          bigserial   PRIMARY KEY,
    for_date    date        NOT NULL UNIQUE,
    content     text        NOT NULL,                   -- markdown
    facts       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    grounded    boolean     NOT NULL DEFAULT false,
    struck      jsonb,
    trace_id    text,
    pushed_at   timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE memos ADD COLUMN IF NOT EXISTS job_id bigint REFERENCES claude_jobs (id);
ALTER TABLE memos ADD COLUMN IF NOT EXISTS struck jsonb;
ALTER TABLE memos ADD COLUMN IF NOT EXISTS what_would_change text;
ALTER TABLE memos ADD COLUMN IF NOT EXISTS facts jsonb;
