"""Document Intelligence (RAG): procurement documents parsed, indexed and searched, with cited answers.

    ingestion/        PDF parsing into pages, sections, tables and chunks (the corpus build)
    retrieval/        hybrid search (bge-m3 vectors + BM25) over a registered release (versions.py)
    answering/        evidence selection, answer generation and claim checks (loaded by wbg_orchestrator/rag_modules.py)
    release_v3/       the pilot_v3 release: 35 projects, 237 documents
    tablelens/        PDF tables as SQL tables
    vector_store/     the Lakebase pgvector store the app searches
    embedding_cache/  per-unit embedding cache used by opt-in releases
"""
