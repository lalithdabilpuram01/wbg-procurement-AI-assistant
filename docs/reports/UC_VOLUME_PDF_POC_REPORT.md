# Unity Catalog managed Volume PDF POC (Step 3): 2026-10-26

**Result:** one approved, public World Bank PDF was stored in a new, isolated Unity Catalog managed Volume in our
Databricks Free Edition workspace, and verified byte for byte in three ways:
- the Volume listing;
- a download of the stored copy;
- a SHA-256 computed inside the workspace by the serverless SQL warehouse.

No other document was uploaded. Nothing existing was changed: Volumes, Bronze/Silver/Gold data, the Lakebase POC, local
PostgreSQL, the RAG pipeline, ML models and manifests are all as they were. No OCR, extraction or model inference was
run.

POC code: `infrastructure/uc_volume_poc/`. It uses only the standard library plus the Databricks CLI, run with the
Lakebase POC's uv environment, so no new dependency was needed. Results, with no identities or credentials:
`infrastructure/uc_volume_poc/results/uc_volume_pdf_poc_20261026T074321Z.json`.

## 1. Read-only inspection and the chosen location

| Item | Finding |
|---|---|
| Catalogs | `workspace` (managed, the only writable one), plus system catalogs `system` and `samples` |
| Schemas in `workspace` | `default`, `wbg_bronze`, `wbg_silver`, `wbg_gold`, `wbg_ref_bronze`, `information_schema` |
| Existing Volumes (all `MANAGED`, all in `workspace.default`) | `my_files`, `raw_data`, `wbg_procurement_raw`, `wbg_procurement_test`, `wbg_reference_raw`. These are the Bronze raw inputs; **none were touched** |
| Our privileges | `USE_CATALOG` on `workspace`; `CREATE_VOLUME` (plus table, function and model privileges) directly on `workspace.default`. No grant was needed |
| Decision | Reuse catalog `workspace` and schema `default`, where the project's raw-file Volumes already live. Create a **new** managed Volume there, separate from every Bronze/Silver/Gold schema and Volume |

## 2. Free Edition support and cost

**Managed Volumes are supported.**
- The [Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations) page
  does not restrict managed Volumes.
- It lists *custom workspace storage locations* as unsupported. That is why we used a managed Volume on the
  metastore's default storage: no external location, storage credential or custom storage was configured.
- The workspace already had five managed Volumes, used for the Bronze uploads, and the new one was created normally.

**Cost:** Free Edition is a no-cost offering under a fair-usage policy, and no paid resource was created.
- The verification query briefly used the existing *Serverless Starter Warehouse* (2X-Small). It had been stopped; the
  query started it, and it auto-stops after 10 idle minutes.
- The documentation gives no storage-quota figures for Free Edition (see §8).

## 3. The PDF

| Field | Value |
|---|---|
| Project / country | **P148850**, Ethiopia: Expressway Development Support Project (one of the 35 approved projects, D-32) |
| Document | **40089196**, `ISR`: "Disclosable Version of the ISR – Ethiopia – Expressway Development Support Project – P148850 – Sequence No: 21" |
| Dates | Document 2026-02-28; publicly disclosed 2026-02-28 |
| Source | `https://documents.worldbank.org/curated/en/099022826132018287/pdf/P148850-247dec36-9215-47c1-8348-e5eaac409aa2.pdf` (World Bank Documents & Reports, public) |
| Local original | `rag/corpus_v3/pdf/ET/P148850/ISR_2026-02-28_40089196.pdf`: 164,080 bytes, 8 pages |
| SHA-256 (corpus manifest = local file) | `c32be7ca2d3d1ce2ea8e2e017cd7ef2cedea0e9aad940711e6f5b4cb7e5a4d24` |

**Identity checks** (`select_document`, offline):
- the document ID is in the approved download manifest `DOCUMENT_DOWNLOAD_MANIFEST_v3_35.csv` and in
  `CORPUS_MANIFEST_FIRST10.csv`;
- its project is in `PROJECT_SELECTION_35.csv`;
- its expected and actual local paths are equal;
- the file is a regular `.pdf` with a `%PDF-` header;
- its size and SHA-256 equal the manifest.

**Privacy:** the pilot's privacy pipeline recorded **0 personal-data masking events** for this document. Masking events
cover person names, emails and phones; only counts were read, never the restricted text. Of the 10 downloaded PDFs, 4
have zero events. Among those, an ISR was chosen over the procurement plans, which can name suppliers. The script
refuses any document with masking events; for example, the P132833 PAD, which has names, an email and a phone, is
refused.

## 4. Upload

The Volume was created with `databricks volumes create workspace default wbg_rag_pdfs_poc MANAGED --comment …`. The
script creates it only if it doesn't exist; an existing Volume is reused unchanged.

The upload was `databricks fs mkdir`, then `databricks fs cp <local> dbfs:<volume path>` (Files API). There is no
`--overwrite` anywhere: if the file already exists, the upload is skipped and the stored copy is verified instead.

**Original filename kept,** in the same `<country>/<project>/<filename>` layout as `rag/corpus_v3/pdf/`.

**Metadata stays outside the Volume.** Project, document, title, dates, source URL, checksum and privacy count are in
the local results JSON and the approved manifests; no sidecar file was uploaded. In the target design this metadata
belongs in the Lakebase document registry.

**Run history, for transparency:**
1. **First attempt:** it created the Volume at 2026-10-26 07:42:33 UTC, then stopped at the copy, because the
   `ET/P148850` sub-folders did not exist. The code was fixed to create the folder (idempotent) before copying.
2. **Second run:** it reused the Volume and uploaded the file. All checks passed (results `…074301Z.json`).
3. **Third run (idempotency check):** it neither recreated the Volume nor re-uploaded the file. All checks passed
   (`…074321Z.json`).

## 5. Verification

| Check | Result |
|---|---|
| Volume listing size | 164,080 = local ✔ |
| Download of the stored copy (`fs cp` to a temporary folder): size | 164,080 ✔ |
| Download of the stored copy: SHA-256 | `c32be7ca…4d24` = local ✔ |
| **Read inside the workspace**: Serverless Starter Warehouse, Statement Execution API, `SELECT path, length, sha2(content, 256) FROM read_files(:path, format => 'binaryFile')` with the path as a bound parameter: length | 164,080 ✔ |
| Read inside the workspace: SHA-256 | `c32be7ca…4d24` ✔ |
| Contents of the Volume | Exactly one file, `ET/P148850/ISR_2026-02-28_40089196.pdf` (164,080 B), plus its two folders |

The workspace read shows that Databricks compute can read the stored bytes. No notebook was deployed, and the content
was only hashed: no OCR, PDF extraction or LLM inference.

## 6. Resource names and future App access

| Item | Value |
|---|---|
| Volume | **`workspace.default.wbg_rag_pdfs_poc`** (`MANAGED`), on the metastore's managed S3 storage |
| Volume root | `/Volumes/workspace/default/wbg_rag_pdfs_poc` |
| **PDF path** | **`/Volumes/workspace/default/wbg_rag_pdfs_poc/ET/P148850/ISR_2026-02-28_40089196.pdf`** |
| Comment | "POC 2026-10-26: approved public World Bank RAG PDFs (35-project scope, D-32). One verification file only; metadata kept outside the Volume." |

**Databricks Apps access, from the documentation; no App was deployed.** A Unity Catalog Volume can be added to an App
as a managed **resource**. See [Apps and UC volumes](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/uc-volumes).
- **Access level:** you choose *Can read* (`READ VOLUME`) or *Can read and write*.
- **Automatic grants:** Databricks grants `USE CATALOG`, `USE SCHEMA` and the chosen Volume privilege to the App's
  service principal, provided the person adding the resource has the right to grant them (for example, `MANAGE` on the
  Volume).
- **Path for the App:** it is exposed through environment variables (`valueFrom`), so the App never needs a hard-coded
  path or credential.

For a RAG App, *Can read* is enough. Free Edition allows up to 3 Apps per account, and each runs for at most 24 hours
after it is started or redeployed.

## 7. Storage consumed

| | Bytes |
|---|---|
| New Volume content | **164,080** (one PDF; folders use no space) |
| Lakebase, local PostgreSQL, other Volumes | Unchanged (0) |

For scale: the full approved corpus would be about 0.09–0.13 GB of PDFs (estimate, see
[PROJECT_STORAGE_ASSESSMENT.md](PROJECT_STORAGE_ASSESSMENT.md) §C). That upload is **not** approved yet.

## 8. Limitations and open questions

**Unresolved:**
- **Storage quota:** the docs publish no Free Edition storage quota for managed Volumes, so the full-corpus fit is not
  verified.
- **Warehouse usage:** the workspace read uses the single Serverless Starter Warehouse and counts towards the
  fair-usage policy. The first statement had to start the warehouse.

**Observed behaviour:**
- **Folders:** `databricks fs cp` does not create parent folders in a Volume, so `fs mkdir` is needed first. This is
  handled in the script.
- **Run timing:** the whole verification took about 18 s, including the warehouse start. This is not a performance
  measurement.
- **Tooling:** Databricks CLI v1.22.1, the copy bundled with the VS Code extension, because no `databricks` is on
  `PATH`. Profile `wbg-databricks-free` (OAuth user login; no secret on disk).

## 9. Manual actions

**None required.**

**Optional:** the warehouse stops by itself after 10 minutes; you can also stop it in the UI.

**To undo this POC:** delete the one file and the Volume (`databricks fs rm …`, `databricks volumes delete
workspace.default.wbg_rag_pdfs_poc`). This needs your approval; it was not done.

## 10. Protected components unchanged

**Unity Catalog:**
- The five pre-existing Volumes are unchanged: name, type, `updated_at` and ID were compared before and after.
- No table, view, schema, grant, external location or storage credential was created or changed.
- No Bronze/Silver/Gold job or notebook was run.

**Other systems:**
- **Lakebase:** not contacted in this step; the `wbg_rag_poc` schema and HNSW POC are untouched.
- **Local PostgreSQL:** not accessed.

**Local files:** an inventory comparison (SHA-256 per file) against the Step 2 state found changes only under
`infrastructure/uc_volume_poc/`:
- the script;
- 5 offline tests (passing);
- `.gitignore`;
- two results JSON files.

Nothing else changed: the RAG pipeline and corpus, the approved manifests, ML models, the Structured Data Agent and the
cleanup archives are the same. No login or token appears in the POC files.

## How to re-run

From `procurement-intelligence/`:

```bash
~/.local/bin/uv run --project infrastructure/lakebase_poc --locked python infrastructure/uc_volume_poc/uc_volume_pdf_poc.py check        # offline
~/.local/bin/uv run --project infrastructure/lakebase_poc --locked pytest -q -p no:cacheprovider infrastructure/uc_volume_poc/tests     # offline
UC_VOLUME_POC_LIVE=1 ~/.local/bin/uv run --project infrastructure/lakebase_poc --locked python infrastructure/uc_volume_poc/uc_volume_pdf_poc.py run   # live, idempotent
```

Sources:
- [Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)
- [Databricks Apps and Unity Catalog volumes](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/uc-volumes)
