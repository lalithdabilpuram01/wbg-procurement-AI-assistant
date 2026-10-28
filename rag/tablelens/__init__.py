"""TableLens: PDF tables as relational tables, found through their descriptions and queried with SQL.

    assemble.py   extracted table parts -> logical tables (continuations joined, columns cleaned, values typed)
    cards.py      the description (card) of each table: document, section, pages, columns, row labels
    store.py      Lakebase: one physical table per PDF table (schema pdf_tables), the schema registry with the
                  description embeddings (wbg_rag.pdf_table_registry), and the release record
    query.py      question -> descriptions -> registry -> validated single-table SQL -> labelled, cited rows
    load.py       CLI: plan (offline) / load / verify (live)

Demo scope: the 10 pilot PDFs (DOCUMENTS). Tables classified "text" (layout tables, narrative grids) are not
loaded for SQL; the normal document search keeps covering them.
"""

DOCUMENTS = ("24440102", "26256705", "33761744", "33851641", "34345370", "34407877", "34455066", "40009757", "40025990",
             "40089196")
RELEASE = "tablelens_pilot"
