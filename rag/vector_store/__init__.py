"""Lakebase PostgreSQL + pgvector store for RAG embeddings (final serving store; schema `wbg_rag`).

    lakebase.py   OAuth connection pool pinned to the schema; query connections are read-only
    index.py      schema, release loading (binary COPY) and filtered HNSW nearest-neighbour search
    encoder.py    the local query/document embedding model (BAAI/bge-m3, offline snapshot)
    load.py       CLI: plan (offline) / load / index / verify (live, explicit confirmation)

The embedding model still runs locally: it encodes every unit once (loaded here) and every question at search time;
pgvector stores the vectors and finds the nearest ones. Keyword (BM25) ranking stays in the application.
"""
