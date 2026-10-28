-- Lesson cards and their review decisions (in the Knowledge Graph schema; the Lesson nodes and edges of ingested
-- documents are in node / edge). Re-ingesting a document refreshes a card's text and tags, never its decision.
CREATE TABLE IF NOT EXISTS {schema}.lesson_card (
    card_id     text PRIMARY KEY,
    project_id  text,
    country     text,
    sector      text,
    document_id text NOT NULL,
    doc_type    text NOT NULL CHECK (doc_type IN ('ICR', 'ICRR', 'PAD')),
    section     text,
    section_id  text,
    page_start  integer,
    page_end    integer,
    headline    text NOT NULL,
    body        text NOT NULL,
    topics      text[] NOT NULL DEFAULT '{}',
    stages      text[] NOT NULL DEFAULT '{}',
    categories  text[] NOT NULL DEFAULT '{}',
    origin      text NOT NULL,                                  -- pilot_v3 (backfill) | ingest
    status      text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected')),
    decided_by  text,
    decided_at  timestamptz,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS lesson_card_status_idx ON {schema}.lesson_card (status, doc_type);
CREATE INDEX IF NOT EXISTS lesson_card_project_idx ON {schema}.lesson_card (project_id);
CREATE INDEX IF NOT EXISTS lesson_card_stages_idx ON {schema}.lesson_card USING gin (stages);
CREATE INDEX IF NOT EXISTS lesson_card_topics_idx ON {schema}.lesson_card USING gin (topics);
