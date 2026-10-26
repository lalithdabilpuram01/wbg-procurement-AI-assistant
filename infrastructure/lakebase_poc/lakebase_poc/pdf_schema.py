"""DDL for the isolated real-PDF evidence POC schema (separate from the synthetic `wbg_rag_poc` schema).

Relationships (all keyed by the immutable document version = document_id + PDF SHA-256 prefix):

    documents 1-n pages
    documents 1-n sections
    documents 1-n retrieval_units 1-1 unit_embeddings          (VECTOR(768), HNSW cosine)
                  retrieval_units 1-1 citations
    documents 1-n parent_chunks (context for strategy-C child chunks)
    table_schemas 1-n pdf_tables                                (future governed PDF-table schema registry)
    documents 1-n pdf_tables 1-n pdf_table_rows 1-n pdf_table_values

PDF tables are stored generically (rows as JSONB cells, typed values as field/value pairs); no physical table is
created per PDF table. Every statement is idempotent (IF NOT EXISTS); nothing is dropped or altered here.
"""
from __future__ import annotations

import re

from psycopg import sql

SCHEMA = "wbg_rag_pdf_poc"
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
TABLES = ("documents", "pages", "sections", "parent_chunks", "retrieval_units", "unit_embeddings", "citations",
          "table_schemas", "pdf_tables", "pdf_table_rows", "pdf_table_values")
HNSW_INDEX = "unit_embeddings_hnsw_cosine"
PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "documents": ("document_version_id",), "pages": ("document_version_id", "page"), "sections": ("section_id",),
    "parent_chunks": ("parent_id",), "retrieval_units": ("unit_id",), "unit_embeddings": ("unit_id",),
    "citations": ("unit_id",), "table_schemas": ("table_schema_id",), "pdf_tables": ("table_id",),
    "pdf_table_rows": ("table_id", "row_index"), "pdf_table_values": ("table_id", "row_index", "field_name"),
}


def checked(identifier: str) -> str:
    if not IDENTIFIER.match(identifier):
        raise ValueError(f"unsafe SQL identifier: {identifier!r}")
    return identifier


# {s} is replaced by the validated, quoted schema identifier.
DDL: tuple[str, ...] = (
    "CREATE SCHEMA IF NOT EXISTS {s}",
    """CREATE TABLE IF NOT EXISTS {s}.documents (
        document_version_id  text PRIMARY KEY,            -- '<document_id>:<sha256[:12]>'
        document_id          text NOT NULL,
        project_id           text NOT NULL CHECK (project_id ~ '^P[0-9]{{6}}$'),
        country_code         text NOT NULL,
        doc_type             text NOT NULL,
        document_role        text,
        title                text NOT NULL,
        document_date        date,
        disclosure_date      date,
        source_url           text NOT NULL,
        sha256               char(64) NOT NULL,
        size_bytes           bigint NOT NULL CHECK (size_bytes > 0),
        page_count           integer NOT NULL CHECK (page_count > 0),
        volume_path          text NOT NULL CHECK (volume_path LIKE '/Volumes/%'),
        extraction_version   text NOT NULL,
        pipeline_code_hash   text NOT NULL,
        loaded_at            timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, sha256))""",
    """CREATE TABLE IF NOT EXISTS {s}.pages (
        document_version_id  text NOT NULL REFERENCES {s}.documents (document_version_id),
        page                 integer NOT NULL CHECK (page > 0),
        printed_page_label   text,
        chars                integer,
        tables_detected      integer,
        PRIMARY KEY (document_version_id, page))""",
    """CREATE TABLE IF NOT EXISTS {s}.sections (
        section_id           text PRIMARY KEY,
        document_version_id  text NOT NULL REFERENCES {s}.documents (document_version_id),
        section_path         text NOT NULL,
        heading_text         text,
        heading_level        integer,
        section_order        integer,
        page_start           integer,
        page_end             integer,
        printed_page_label_start text,
        printed_page_label_end   text)""",
    """CREATE TABLE IF NOT EXISTS {s}.parent_chunks (
        parent_id            text PRIMARY KEY,
        document_version_id  text NOT NULL REFERENCES {s}.documents (document_version_id),
        section_path         text,
        page_start           integer NOT NULL,
        page_end             integer NOT NULL,
        text                 text NOT NULL,
        child_ids            text[] NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS {s}.retrieval_units (
        unit_id              text PRIMARY KEY,
        document_version_id  text NOT NULL REFERENCES {s}.documents (document_version_id),
        unit_type            text NOT NULL CHECK (unit_type IN ('chunk', 'table')),
        strategy_version     text NOT NULL,
        section_id           text,
        section_path         text,
        parent_id            text REFERENCES {s}.parent_chunks (parent_id),
        table_id             text,
        page_start           integer NOT NULL,
        page_end             integer NOT NULL CHECK (page_end >= page_start),
        printed_page_label   text,
        indexed_text         text NOT NULL,              -- exactly the text that was embedded
        excerpt              text NOT NULL,              -- sanitised text shown to users
        indexed_text_sha256  char(64) NOT NULL,
        source_spans         jsonb NOT NULL DEFAULT '[]'::jsonb,   -- paragraph/page spans the unit was built from
        sanitisation         jsonb NOT NULL DEFAULT '{{}}'::jsonb)""",
    """CREATE TABLE IF NOT EXISTS {s}.unit_embeddings (
        unit_id              text PRIMARY KEY REFERENCES {s}.retrieval_units (unit_id),
        model                text NOT NULL,
        preprocessing        jsonb NOT NULL,             -- e.g. {{"normalize_embeddings": true, "input": "indexed_text"}}
        source_cache         text NOT NULL,              -- cached .npy file the vector was read from
        embedding            vector(768) NOT NULL,
        embedding_sha256     char(64) NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS {hnsw} ON {s}.unit_embeddings USING hnsw (embedding vector_cosine_ops)",
    """CREATE TABLE IF NOT EXISTS {s}.citations (
        unit_id              text PRIMARY KEY REFERENCES {s}.retrieval_units (unit_id),
        document_version_id  text NOT NULL REFERENCES {s}.documents (document_version_id),
        project_id           text NOT NULL,
        document_id          text NOT NULL,
        page_start           integer NOT NULL,
        page_end             integer NOT NULL,
        printed_page_label   text,
        section_path         text,
        source_url           text NOT NULL,
        volume_path          text NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS {s}.table_schemas (
        table_schema_id      text PRIMARY KEY,            -- e.g. 'wb_isr_results_indicators_v1'
        description          text NOT NULL,
        fields               jsonb NOT NULL,              -- [{{"name", "type", "unit_source"}}, ...]
        status               text NOT NULL CHECK (status IN ('draft', 'approved', 'retired')))""",
    """CREATE TABLE IF NOT EXISTS {s}.pdf_tables (
        table_id             text PRIMARY KEY,
        document_version_id  text NOT NULL REFERENCES {s}.documents (document_version_id),
        table_group_id       text NOT NULL,               -- logical table spanning continuation parts
        table_schema_id      text REFERENCES {s}.table_schemas (table_schema_id),
        page                 integer NOT NULL,
        caption              text,
        section_path         text,
        header               jsonb NOT NULL,
        n_columns            integer NOT NULL,
        n_data_rows          integer NOT NULL,
        continuation_of      text,
        description          text,                        -- searchable description of the logical table
        extraction_version   text NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS {s}.pdf_table_rows (
        table_id             text NOT NULL REFERENCES {s}.pdf_tables (table_id),
        row_index            integer NOT NULL,
        page                 integer NOT NULL,
        is_header            boolean NOT NULL,
        cells                jsonb NOT NULL,              -- sanitised cells, original column order
        PRIMARY KEY (table_id, row_index))""",
    """CREATE TABLE IF NOT EXISTS {s}.pdf_table_values (
        table_id             text NOT NULL,
        row_index            integer NOT NULL,
        field_name           text NOT NULL,
        value_text           text,
        value_numeric        numeric,
        unit                 text,
        quality              text NOT NULL CHECK (quality IN ('complete_row', 'needs_review')),
        PRIMARY KEY (table_id, row_index, field_name),
        FOREIGN KEY (table_id, row_index) REFERENCES {s}.pdf_table_rows (table_id, row_index))""",
)


def statements(schema: str = SCHEMA) -> list[sql.Composed]:
    s, hnsw = sql.Identifier(checked(schema)), sql.Identifier(checked(HNSW_INDEX))
    return [sql.SQL(statement).format(s=s, hnsw=hnsw) for statement in DDL]


def cleanup_statement(schema: str = SCHEMA) -> sql.Composed:
    """Rollback for this POC only (not executed by any code path; documented for an approved manual cleanup)."""
    if checked(schema) != SCHEMA:
        raise ValueError("cleanup is limited to the PDF POC schema")
    return sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
