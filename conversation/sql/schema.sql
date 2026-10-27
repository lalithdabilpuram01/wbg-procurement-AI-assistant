-- Conversation store schema (non-destructive: CREATE ... IF NOT EXISTS only; never DROP or ALTER existing data).
-- Applied by conversation/init_store.py as wbg_conversation_admin; {schema} is `conversation` (or a test schema).
--
-- Isolation is enforced twice: every store query filters by tenant and user, and row-level security limits the
-- runtime role to rows of the tenant/user set for the transaction (wbg.tenant_id, wbg.user_id).

CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.threads (
    thread_id    uuid PRIMARY KEY,
    tenant_id    text NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 100),
    user_id      text NOT NULL CHECK (length(user_id) BETWEEN 1 AND 100),
    title        text NOT NULL CHECK (length(title) <= 200),
    topic_epoch  integer NOT NULL DEFAULT 0 CHECK (topic_epoch >= 0),
    created_at   timestamptz NOT NULL,
    updated_at   timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS threads_owner_idx ON {schema}.threads (tenant_id, user_id, updated_at DESC);

-- Original turns: append-only (the runtime role has no UPDATE or DELETE on this table).
CREATE TABLE IF NOT EXISTS {schema}.turns (
    turn_id      uuid PRIMARY KEY,
    thread_id    uuid NOT NULL REFERENCES {schema}.threads (thread_id),
    seq          integer NOT NULL CHECK (seq > 0),
    role         text NOT NULL CHECK (role IN ('user', 'assistant')),
    kind         text NOT NULL,
    text         text NOT NULL CHECK (length(text) <= 4000),
    topic_epoch  integer NOT NULL,
    context      jsonb NOT NULL,        -- active entities and filters after this turn
    metadata     jsonb NOT NULL,        -- status, validated SQL, evidence ids; never result rows or credentials
    created_at   timestamptz NOT NULL,
    UNIQUE (thread_id, seq)
);

CREATE TABLE IF NOT EXISTS {schema}.decisions (
    decision_id      uuid PRIMARY KEY,
    thread_id        uuid NOT NULL REFERENCES {schema}.threads (thread_id),
    kind             text NOT NULL CHECK (kind IN ('decision', 'constraint')),
    statement        text NOT NULL CHECK (length(statement) BETWEEN 3 AND 500),
    approved_by      text NOT NULL,
    source_turn_seq  integer,
    created_at       timestamptz NOT NULL,
    revoked_at       timestamptz,
    revoked_reason   text
);

-- Compressed memory: stored separately from the original turns, versioned, invalidated but never edited.
CREATE TABLE IF NOT EXISTS {schema}.summaries (
    summary_id           uuid PRIMARY KEY,
    thread_id            uuid NOT NULL REFERENCES {schema}.threads (thread_id),
    version              integer NOT NULL CHECK (version > 0),
    topic_epoch          integer NOT NULL,
    covers_from_seq      integer NOT NULL,
    covers_through_seq   integer NOT NULL CHECK (covers_through_seq >= covers_from_seq),
    content              jsonb NOT NULL,
    generator            text NOT NULL,
    inputs_sha256        text NOT NULL,
    registry_build_id    text,
    created_at           timestamptz NOT NULL,
    invalidated_at       timestamptz,
    invalidation_reason  text,
    UNIQUE (thread_id, version)
);

-- Row-level security: the runtime role sees only rows of the owner set for its transaction.
ALTER TABLE {schema}.threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.summaries ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = '{schema_literal}' AND policyname = 'owner_threads') THEN
        CREATE POLICY owner_threads ON {schema}.threads
            USING (tenant_id = current_setting('wbg.tenant_id', true) AND user_id = current_setting('wbg.user_id', true));
        CREATE POLICY owner_turns ON {schema}.turns
            USING (thread_id IN (SELECT thread_id FROM {schema}.threads));
        CREATE POLICY owner_decisions ON {schema}.decisions
            USING (thread_id IN (SELECT thread_id FROM {schema}.threads));
        CREATE POLICY owner_summaries ON {schema}.summaries
            USING (thread_id IN (SELECT thread_id FROM {schema}.threads));
    END IF;
END
$$;

GRANT USAGE ON SCHEMA {schema} TO wbg_conversation_app;
GRANT SELECT, INSERT ON {schema}.threads, {schema}.turns, {schema}.decisions, {schema}.summaries TO wbg_conversation_app;
GRANT UPDATE (title, topic_epoch, updated_at) ON {schema}.threads TO wbg_conversation_app;
GRANT UPDATE (revoked_at, revoked_reason) ON {schema}.decisions TO wbg_conversation_app;
GRANT UPDATE (invalidated_at, invalidation_reason) ON {schema}.summaries TO wbg_conversation_app;

-- Workspace (Milestone F): folders that group conversations, and bookmarks on answers. Owner-scoped like the threads.
CREATE TABLE IF NOT EXISTS {schema}.folders (
    folder_id   uuid PRIMARY KEY,
    tenant_id   text NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 100),
    user_id     text NOT NULL CHECK (length(user_id) BETWEEN 1 AND 100),
    name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    created_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS folders_owner_idx ON {schema}.folders (tenant_id, user_id);

CREATE TABLE IF NOT EXISTS {schema}.thread_folders (
    thread_id  uuid PRIMARY KEY REFERENCES {schema}.threads (thread_id),
    folder_id  uuid NOT NULL REFERENCES {schema}.folders (folder_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS {schema}.bookmarks (
    bookmark_id  uuid PRIMARY KEY,
    thread_id    uuid NOT NULL REFERENCES {schema}.threads (thread_id),
    turn_seq     integer NOT NULL CHECK (turn_seq > 0),
    note         text NOT NULL DEFAULT '' CHECK (length(note) <= 200),
    created_at   timestamptz NOT NULL,
    UNIQUE (thread_id, turn_seq)
);

ALTER TABLE {schema}.folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.thread_folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.bookmarks ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = '{schema_literal}' AND policyname = 'owner_folders') THEN
        CREATE POLICY owner_folders ON {schema}.folders
            USING (tenant_id = current_setting('wbg.tenant_id', true) AND user_id = current_setting('wbg.user_id', true));
        CREATE POLICY owner_thread_folders ON {schema}.thread_folders
            USING (thread_id IN (SELECT thread_id FROM {schema}.threads));
        CREATE POLICY owner_bookmarks ON {schema}.bookmarks
            USING (thread_id IN (SELECT thread_id FROM {schema}.threads));
    END IF;
END
$$;

GRANT SELECT, INSERT, DELETE ON {schema}.folders, {schema}.thread_folders, {schema}.bookmarks TO wbg_conversation_app;
GRANT UPDATE (name) ON {schema}.folders TO wbg_conversation_app;
GRANT UPDATE (folder_id) ON {schema}.thread_folders TO wbg_conversation_app;
