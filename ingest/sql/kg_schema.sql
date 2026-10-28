-- Procurement Knowledge Graph (Lakebase / PostgreSQL). Non-destructive: CREATE ... IF NOT EXISTS only.
-- Applied by `python -m ingest init-db`; {schema} is `wbg_kg` (or a test schema); {vector} is public.halfvec(1024)
-- where pgvector is installed (real[] otherwise).
--
-- Two layers over the same facts:
--   * typed relational tables, the system of record: lineage (source_file, ingest_run), the document hierarchy
--     (document -> section tree -> chunk; doc_table -> doc_table_row), procurement records (plan activities,
--     contracts, notices) and entity mentions with their spans;
--   * a property graph (node, edge), the integration layer: one node per real-world entity whatever file it came
--     from, and typed edges with match class, confidence and evidence. The edge table is LIST-partitioned by
--     relationship family, so traversals of one family scan one partition and new families are added without
--     rewriting existing data.

CREATE SCHEMA IF NOT EXISTS {schema};

-- ------------------------------------------------------------------------------------------------- lineage
CREATE TABLE IF NOT EXISTS {schema}.ingest_run (
    run_id        text PRIMARY KEY,
    started_at    timestamptz NOT NULL,
    finished_at   timestamptz,
    pipeline      text NOT NULL,
    summary       jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS {schema}.source_file (
    sha256           text PRIMARY KEY,
    file_name        text NOT NULL,
    folder           text NOT NULL,
    detected_format  text,
    modality         text,
    size_bytes       bigint,
    document_id      text,
    status           text NOT NULL CHECK (status IN ('processed', 'quarantined', 'duplicate')),
    reason           text,
    warnings         jsonb NOT NULL DEFAULT '[]',
    rejected_rows    jsonb NOT NULL DEFAULT '[]',
    run_id           text,
    pipeline         text NOT NULL,
    processed_at     timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS source_file_document_idx ON {schema}.source_file (document_id);

-- --------------------------------------------------------------------------------------- document hierarchy
CREATE TABLE IF NOT EXISTS {schema}.document (
    document_id    text PRIMARY KEY,
    doc_type       text NOT NULL,
    modality       text NOT NULL,
    title          text,
    project_id     text,
    country_code   text,
    document_date  date,
    language       text,
    page_count     integer,
    source_sha256  text NOT NULL,
    ingested_at    timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS document_project_idx ON {schema}.document (project_id, doc_type, document_date);

CREATE TABLE IF NOT EXISTS {schema}.section (
    section_id         text PRIMARY KEY,
    document_id        text NOT NULL,
    parent_section_id  text,
    level              integer NOT NULL,
    ord                integer NOT NULL,
    heading            text NOT NULL,
    path               text NOT NULL,
    kind               text NOT NULL,
    topic              text,
    page_start         integer,
    page_end           integer
);
CREATE INDEX IF NOT EXISTS section_document_idx ON {schema}.section (document_id, ord);
CREATE INDEX IF NOT EXISTS section_topic_idx ON {schema}.section (topic) WHERE topic IS NOT NULL;

CREATE TABLE IF NOT EXISTS {schema}.chunk (
    chunk_id     text PRIMARY KEY,
    document_id  text NOT NULL,
    section_id   text NOT NULL,
    ord          integer NOT NULL,
    text         text NOT NULL,
    token_count  integer NOT NULL,
    page_start   integer,
    page_end     integer,
    embedding    {vector}
);
CREATE INDEX IF NOT EXISTS chunk_document_idx ON {schema}.chunk (document_id, ord);
CREATE INDEX IF NOT EXISTS chunk_section_idx ON {schema}.chunk (section_id);

CREATE TABLE IF NOT EXISTS {schema}.doc_table (
    table_id     text PRIMARY KEY,
    document_id  text NOT NULL,
    section_id   text,
    page         integer,
    caption      text,
    header       jsonb NOT NULL,
    n_rows       integer NOT NULL
);
CREATE INDEX IF NOT EXISTS doc_table_document_idx ON {schema}.doc_table (document_id);

CREATE TABLE IF NOT EXISTS {schema}.doc_table_row (
    table_id   text NOT NULL,
    row_index  integer NOT NULL,
    cells      jsonb NOT NULL,
    PRIMARY KEY (table_id, row_index)
);

-- ---------------------------------------------------------------------------------- procurement records
CREATE TABLE IF NOT EXISTS {schema}.plan_activity (
    activity_id           text PRIMARY KEY,
    document_id           text NOT NULL,
    project_id            text,
    reference             text,
    reference_norm        text,
    description           text,
    method                text,
    category              text,
    review_type           text,
    status                text,
    estimated_amount_usd  numeric,
    actual_amount_usd     numeric,
    planned_signing_date  text,
    actual_signing_date   text,
    source_row            integer
);
CREATE INDEX IF NOT EXISTS plan_activity_reference_idx ON {schema}.plan_activity (project_id, reference_norm);

CREATE TABLE IF NOT EXISTS {schema}.contract (
    contract_key        text PRIMARY KEY,
    document_id         text NOT NULL,
    project_id          text,
    wb_contract_number  text,
    reference           text,
    reference_norm      text,
    amount_usd          numeric,
    signing_date        date,
    method              text,
    category            text,
    review_type         text,
    supplier_name       text,
    supplier_country    text,
    description         text,
    extra               jsonb NOT NULL DEFAULT '{}',
    source_row          integer
);
CREATE INDEX IF NOT EXISTS contract_reference_idx ON {schema}.contract (project_id, reference_norm);

CREATE TABLE IF NOT EXISTS {schema}.notice (
    notice_key        text PRIMARY KEY,
    document_id       text NOT NULL,
    project_id        text,
    notice_id         text,
    notice_type       text,
    reference         text,
    reference_norm    text,
    publication_date  date,
    deadline_date     date,
    method            text,
    category          text,
    description       text,
    extra             jsonb NOT NULL DEFAULT '{}',
    source_row        integer
);
CREATE INDEX IF NOT EXISTS notice_reference_idx ON {schema}.notice (project_id, reference_norm);

CREATE TABLE IF NOT EXISTS {schema}.entity_mention (
    chunk_id     text NOT NULL,
    document_id  text NOT NULL,
    entity_type  text NOT NULL,
    value        text NOT NULL,
    node_id      text,
    match_class  text NOT NULL,
    confidence   real NOT NULL,
    span_start   integer NOT NULL,
    span_end     integer NOT NULL,
    PRIMARY KEY (chunk_id, entity_type, span_start)
);
CREATE INDEX IF NOT EXISTS entity_mention_node_idx ON {schema}.entity_mention (node_id);

-- ------------------------------------------------------------------------------------------ property graph
CREATE TABLE IF NOT EXISTS {schema}.node (
    node_id      text PRIMARY KEY,
    node_type    text NOT NULL,
    label        text NOT NULL,
    props        jsonb NOT NULL DEFAULT '{}',
    document_id  text,                 -- set for document-owned nodes (replaced with their document)
    updated_at   timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS node_type_idx ON {schema}.node (node_type);
CREATE INDEX IF NOT EXISTS node_document_idx ON {schema}.node (document_id) WHERE document_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS {schema}.edge (
    edge_type    text NOT NULL,
    src          text NOT NULL,
    dst          text NOT NULL,
    match_class  text NOT NULL,
    confidence   real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence     jsonb NOT NULL DEFAULT '{}',
    document_id  text,                 -- the document that asserted it; NULL for edges found by reconciliation
    origin       text NOT NULL,        -- parser | resolver | reconcile
    updated_at   timestamptz NOT NULL,
    PRIMARY KEY (edge_type, src, dst)
) PARTITION BY LIST (edge_type);
CREATE TABLE IF NOT EXISTS {schema}.edge_hierarchy PARTITION OF {schema}.edge
    FOR VALUES IN ('HAS_DOCUMENT', 'HAS_SECTION', 'PARENT_OF', 'HAS_CHUNK', 'NEXT', 'HAS_TABLE');
CREATE TABLE IF NOT EXISTS {schema}.edge_semantic PARTITION OF {schema}.edge
    FOR VALUES IN ('ABOUT_TOPIC', 'MENTIONS');
CREATE TABLE IF NOT EXISTS {schema}.edge_procurement PARTITION OF {schema}.edge
    FOR VALUES IN ('LISTS_ACTIVITY', 'ACTIVITY_OF', 'PLANNED_AS', 'AWARDED_UNDER', 'AWARDED_TO', 'USES_METHOD', 'NOTICE_FOR', 'NOTICE_OF');
CREATE TABLE IF NOT EXISTS {schema}.edge_context PARTITION OF {schema}.edge
    FOR VALUES IN ('IN_COUNTRY', 'IN_SECTOR', 'TEMPORALLY_NEAR');
CREATE TABLE IF NOT EXISTS {schema}.edge_other PARTITION OF {schema}.edge DEFAULT;
CREATE INDEX IF NOT EXISTS edge_src_idx ON {schema}.edge (src, edge_type);
CREATE INDEX IF NOT EXISTS edge_dst_idx ON {schema}.edge (dst, edge_type);
CREATE INDEX IF NOT EXISTS edge_document_idx ON {schema}.edge (document_id);
