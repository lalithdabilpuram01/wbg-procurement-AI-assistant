-- Feedback and learning store (Module 4) in Lakebase: its own schema on a development branch, next to (never inside)
-- the checkpoint schema. Non-destructive: CREATE ... IF NOT EXISTS only; nothing is dropped or altered.
-- Applied by `python -m wbg_orchestrator.learning_db init`; {schema} is `wbg_learning` (or a test schema).
--
-- Conversations live in another database, so `thread_id` has no foreign key: the app checks thread ownership in the
-- conversation store before anything is written here. The app connects as a single Lakebase identity that owns these
-- tables, so row-level security is FORCED (it applies to the owner too): each transaction sets the trusted owner
-- (wbg.tenant_id, wbg.user_id) and sees only that owner's rows. Feedback, preferences and uses are per user; learned
-- items are the owner's plus the tenant's shared ones; reviews are tenant-wide (reviewer rights are checked by the app).
-- `operator_read` lets only the operator report (learning_cli, wbg.operator = 'report') read across users.

CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.feedback (
    feedback_id          uuid PRIMARY KEY,
    tenant_id            text NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 100),
    user_id              text NOT NULL CHECK (length(user_id) BETWEEN 1 AND 100),
    thread_id            uuid NOT NULL,
    turn_seq             integer NOT NULL CHECK (turn_seq > 0),
    request_id           text,
    question             text NOT NULL DEFAULT '' CHECK (length(question) <= 2000),
    rating               text NOT NULL CHECK (rating IN ('up', 'down')),
    reasons              text[] NOT NULL DEFAULT '{}',
    correction           text CHECK (length(correction) <= 500),
    status               text NOT NULL CHECK (status IN ('recorded', 'revising', 'revised', 'validated')),
    revision_request_id  text,
    created_at           timestamptz NOT NULL,
    updated_at           timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS feedback_thread_idx ON {schema}.feedback (tenant_id, user_id, thread_id, turn_seq);
CREATE INDEX IF NOT EXISTS feedback_revision_idx ON {schema}.feedback (revision_request_id);

CREATE TABLE IF NOT EXISTS {schema}.preferences (
    tenant_id   text NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 100),
    user_id     text NOT NULL CHECK (length(user_id) BETWEEN 1 AND 100),
    key         text NOT NULL CHECK (key IN ('default_fiscal_years', 'amount_unit', 'answer_length', 'table_lens')),
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, user_id, key)
);

CREATE TABLE IF NOT EXISTS {schema}.learned_items (
    item_id             uuid PRIMARY KEY,
    tenant_id           text NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 100),
    user_id             text NOT NULL CHECK (length(user_id) BETWEEN 1 AND 100),   -- whose feedback it came from
    scope               text NOT NULL CHECK (scope IN ('personal', 'shared')),
    kind                text NOT NULL CHECK (kind IN ('correction', 'approach')),
    statement           text NOT NULL CHECK (length(statement) BETWEEN 3 AND 500),
    question            text NOT NULL CHECK (length(question) <= 2000),
    details             jsonb NOT NULL,
    embedding           real[],
    source_feedback_id  uuid,
    approved_by         text,
    status              text NOT NULL CHECK (status IN ('active', 'revoked')),
    use_count           integer NOT NULL DEFAULT 0,
    last_used_at        timestamptz,
    created_at          timestamptz NOT NULL,
    revoked_at          timestamptz
);
CREATE INDEX IF NOT EXISTS learned_items_owner_idx ON {schema}.learned_items (tenant_id, user_id, status);

CREATE TABLE IF NOT EXISTS {schema}.learning_uses (
    request_id  text NOT NULL,
    item_id     uuid NOT NULL REFERENCES {schema}.learned_items (item_id),
    tenant_id   text NOT NULL,
    user_id     text NOT NULL,
    created_at  timestamptz NOT NULL,
    PRIMARY KEY (request_id, item_id)
);

CREATE TABLE IF NOT EXISTS {schema}.reviews (
    review_id           uuid PRIMARY KEY,
    tenant_id           text NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 100),
    submitted_by        text NOT NULL,
    kind                text NOT NULL CHECK (kind IN ('share_correction', 'share_approach', 'eval_case')),
    payload             jsonb NOT NULL,
    source_feedback_id  uuid,
    status              text NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewer            text,
    note                text CHECK (length(note) <= 300),
    expected_terms      text[] NOT NULL DEFAULT '{}',
    created_at          timestamptz NOT NULL,
    decided_at          timestamptz
);
CREATE INDEX IF NOT EXISTS reviews_tenant_idx ON {schema}.reviews (tenant_id, status, created_at);

ALTER TABLE {schema}.feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.learned_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.learning_uses ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.feedback FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.preferences FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.learned_items FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.learning_uses FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.reviews FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = '{schema_literal}' AND policyname = 'owner_feedback') THEN
        CREATE POLICY owner_feedback ON {schema}.feedback
            USING (tenant_id = current_setting('wbg.tenant_id', true) AND user_id = current_setting('wbg.user_id', true));
        CREATE POLICY owner_preferences ON {schema}.preferences
            USING (tenant_id = current_setting('wbg.tenant_id', true) AND user_id = current_setting('wbg.user_id', true));
        CREATE POLICY owner_or_shared_items ON {schema}.learned_items
            USING (tenant_id = current_setting('wbg.tenant_id', true)
                   AND (user_id = current_setting('wbg.user_id', true) OR scope = 'shared'));
        CREATE POLICY owner_learning_uses ON {schema}.learning_uses
            USING (tenant_id = current_setting('wbg.tenant_id', true) AND user_id = current_setting('wbg.user_id', true));
        CREATE POLICY tenant_reviews ON {schema}.reviews
            USING (tenant_id = current_setting('wbg.tenant_id', true));
        CREATE POLICY operator_read ON {schema}.feedback FOR SELECT USING (current_setting('wbg.operator', true) = 'report');
        CREATE POLICY operator_read ON {schema}.learned_items FOR SELECT USING (current_setting('wbg.operator', true) = 'report');
        CREATE POLICY operator_read ON {schema}.learning_uses FOR SELECT USING (current_setting('wbg.operator', true) = 'report');
        CREATE POLICY operator_read ON {schema}.reviews FOR SELECT USING (current_setting('wbg.operator', true) = 'report');
    END IF;
END
$$;
