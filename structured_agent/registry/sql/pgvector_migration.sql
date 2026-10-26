-- NOT APPLIED. Migration for when the pgvector extension is approved and installed on the server.
-- Converts the real[] embeddings to a vector column with an HNSW cosine index; the VectorIndex interface in
-- registry/vector_index.py then switches from NumpyCosineIndex to a pgvector-backed implementation.

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE registry.description_embeddings ADD COLUMN embedding_vec vector(768);
UPDATE registry.description_embeddings SET embedding_vec = embedding::vector(768);
ALTER TABLE registry.description_embeddings ALTER COLUMN embedding_vec SET NOT NULL;
CREATE INDEX description_embeddings_hnsw ON registry.description_embeddings USING hnsw (embedding_vec vector_cosine_ops);
