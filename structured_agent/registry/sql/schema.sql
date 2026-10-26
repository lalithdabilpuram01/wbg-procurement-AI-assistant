-- Static Table Registry schema (PostgreSQL 16). Applied by init_registry.py as wbg_registry_admin.
-- Re-running is safe: the registry is rebuilt from scratch inside one transaction (no scheduled synchronisation).
--
-- metadata_status values used throughout:
--   verified  = read from Databricks metadata or confirmed by a live read-only data check
--   curated   = written by hand, grounded in existing code or documentation (source recorded)
--   unverified = needs review before it is trusted

DROP SCHEMA IF EXISTS registry CASCADE;
CREATE SCHEMA registry;

CREATE TABLE registry.registry_builds (
    build_id            text PRIMARY KEY,
    built_at            timestamptz NOT NULL,
    databricks_snapshot text NOT NULL,          -- discovery snapshot file name
    snapshot_sha256     text NOT NULL,
    verification_file   text NOT NULL,          -- live metadata verification file name
    curated_sha256      text NOT NULL,          -- hash of the curated JSON inputs
    embedding_model     text NOT NULL,
    notes               text
);

CREATE TABLE registry.data_classifications (
    classification text PRIMARY KEY,
    description    text NOT NULL,
    source         text NOT NULL
);

CREATE TABLE registry.registered_objects (
    object_fqn            text PRIMARY KEY,     -- workspace.<schema>.<name>
    logical_name          text NOT NULL,
    layer                 text NOT NULL CHECK (layer IN ('silver', 'gold')),
    object_type           text NOT NULL CHECK (object_type IN ('table', 'view')),
    certified_aggregate   boolean NOT NULL,     -- approved Gold KPI view: preferred for certified aggregations
    short_description     text NOT NULL,
    description_source    text NOT NULL,
    grain                 text NOT NULL,
    grain_columns         text[] NOT NULL,
    grain_source          text NOT NULL,
    grain_status          text NOT NULL CHECK (grain_status IN ('verified', 'curated', 'unverified')),
    row_count             bigint NOT NULL,
    authorized_roles      text[] NOT NULL,
    authorized_operations text[] NOT NULL,
    required_filters      jsonb NOT NULL DEFAULT '[]',
    limitations           text[] NOT NULL DEFAULT '{}',
    provenance            jsonb NOT NULL,
    build_id              text NOT NULL REFERENCES registry.registry_builds
);

CREATE TABLE registry.registered_columns (
    object_fqn         text NOT NULL REFERENCES registry.registered_objects ON DELETE CASCADE,
    column_name        text NOT NULL,
    position           int NOT NULL,
    data_type          text NOT NULL,
    nullable           boolean NOT NULL,
    description        text,
    description_source text NOT NULL,
    classification     text NOT NULL REFERENCES registry.data_classifications,
    policy_source      text NOT NULL,
    unit               text,
    measure_family     text,                   -- amounts of different families are never combined
    additive           boolean NOT NULL,       -- false: SUM is not meaningful (percentiles, rates, scores)
    PRIMARY KEY (object_fqn, column_name)
);

CREATE TABLE registry.relationships (
    relationship_id     text PRIMARY KEY,
    from_object         text NOT NULL REFERENCES registry.registered_objects,
    from_columns        text[] NOT NULL,
    to_object           text NOT NULL REFERENCES registry.registered_objects,
    to_columns          text[] NOT NULL,
    cardinality         text NOT NULL CHECK (cardinality IN ('N:1', '1:1', '1:0..1')),
    required_conditions jsonb NOT NULL DEFAULT '[]',
    aggregation_rule    text NOT NULL,
    source              text NOT NULL,
    status              text NOT NULL CHECK (status IN ('verified', 'curated', 'unverified')),
    verification        jsonb NOT NULL
);

CREATE TABLE registry.metrics (
    metric_id         text PRIMARY KEY,
    name              text NOT NULL,
    description       text NOT NULL,
    object_fqn        text NOT NULL REFERENCES registry.registered_objects,
    expression        text NOT NULL,
    unit              text NOT NULL,
    grain_note        text NOT NULL,
    ambiguity_group   text,
    caveats           text[] NOT NULL DEFAULT '{}',
    certified_source  text,                    -- certified view column that already holds this metric
    source            text NOT NULL,
    status            text NOT NULL CHECK (status IN ('verified', 'curated', 'unverified'))
);

CREATE TABLE registry.synonyms (
    term        text NOT NULL,
    target_kind text NOT NULL CHECK (target_kind IN ('object', 'column', 'metric')),
    target_ref  text NOT NULL,
    note        text,
    source      text NOT NULL,
    status      text NOT NULL CHECK (status IN ('verified', 'curated', 'unverified')),
    PRIMARY KEY (term, target_kind, target_ref)
);

CREATE TABLE registry.verified_sql_examples (
    example_id          text PRIMARY KEY,
    question            text NOT NULL,
    sql_text            text NOT NULL,
    parameters          jsonb NOT NULL DEFAULT '{}',
    objects             text[] NOT NULL,
    metrics             text[] NOT NULL DEFAULT '{}',
    expected_result     jsonb,
    verification_status text NOT NULL CHECK (verification_status IN ('verified', 'curated', 'unverified')),
    verification_note   text NOT NULL
);

-- Description embeddings stored as real[] (pgvector is not installed on this server).
-- Migration path: registry/sql/pgvector_migration.sql.
CREATE TABLE registry.description_embeddings (
    doc_id      text PRIMARY KEY,
    doc_kind    text NOT NULL CHECK (doc_kind IN ('object', 'column', 'metric')),
    object_fqn  text NOT NULL REFERENCES registry.registered_objects,
    column_name text,
    metric_id   text,
    doc_text    text NOT NULL,
    model_id    text NOT NULL,
    dimensions  int NOT NULL CHECK (dimensions > 0),
    embedding   real[] NOT NULL,
    CHECK (cardinality(embedding) = dimensions)
);

-- Prepared for durable audit storage (the current runtime writes audit events to a local JSONL sink).
CREATE TABLE registry.audit_events (
    event_id      text PRIMARY KEY,
    occurred_at   timestamptz NOT NULL,
    request_id    text NOT NULL,
    invocation_id text NOT NULL,
    event_type    text NOT NULL,
    attributes    jsonb NOT NULL
);

GRANT USAGE ON SCHEMA registry TO wbg_registry_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA registry TO wbg_registry_reader;
