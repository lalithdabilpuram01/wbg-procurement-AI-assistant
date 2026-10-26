# D-14: Personal Contact Data in Procurement-Notice Text

| | |
|---|---|
| Status | **Approved 2026-10-23.** Direction extended 2026-10-23 to Bronze and raw file storage, with least privilege. **Access audit done (read-only); proposed changes await approval** |
| Scope | `notice_text` of the procnotices API records: Bronze `wbg_bronze.bronze_procurement_notices_api.raw_record` and Silver `wbg_silver.procurement_notice_details.notice_text` |
| Evidence | Access-control probe `silver/runs/d14_access_probe_20261023T173456Z/access_control_probe.json`; production write and verification `silver/runs/notices_20261023T175322Z/` (`FINAL_DQ_REPORT.md`, `post_write_verification.json`) |

## Decision

1. **Preserve the text.** Silver keeps the published notice text exactly as published.
2. **Treat it as restricted data.** Every non-NULL `notice_text` is restricted, because contact-data detection is not
   exhaustive. In the 20,169 texts, the detectors found e-mail addresses in 4,425 and labelled phone numbers in 2,970.
3. **Never expose unrestricted text.** It must not reach the frontend, external APIs, RAG indexes, embeddings or logs.
4. **No plain-text column in Silver.**
   - Decoded, sanitised text is produced later, in the document/RAG preparation pipeline, in Python.
   - That pipeline must have tests for HTML entities, Unicode, e-mail addresses, phone numbers and sanitisation.
   - No raw text may enter the RAG index.

## Controls implemented

| Control | Where | How it is enforced or verified |
|---|---|---|
| Column mask on `notice_text` | `workspace.wbg_silver.procurement_notice_details` | Mask function `workspace.wbg_silver.mask_restricted_notice_text`. It returns the text only to members of the account group `wbg_restricted_notice_text`, and a fixed placeholder to everyone else; NULL stays NULL. The mask is declared in `CREATE TABLE` and confirmed in `information_schema.column_masks` **before any row is inserted** (`silver/common/write.py`) |
| No readers by default | Workspace groups | The group `wbg_restricted_notice_text` does not exist (the workspace has only `admins` and `users`), so no identity, including the owner, reads the raw text |
| Fidelity without reading text | `notice_text_sha256` | Computed from the unmasked Bronze value at write time; the verifier compares it with SHA-256 of the Volume source text |
| Detection flags | `notice_text_contains_email`, `notice_text_contains_phone`, `notice_text_access_class = 'restricted'` | Flags only; the whole column is masked regardless. Spark/Python parity is a blocking check |
| Text isolated to one column | All four notices Silver tables | Verified: `notice_text` exists only in the details table, and no other string column of the four tables contains an e-mail address (blocking check). Phone-like patterns elsewhere are false positives ("approximately", "Intel"), and the word-boundary rule removes them |
| Logs and reports | `silver/runs/*` JSON and console output | Jobs print only counts and IDs for notice text, never the text itself. The probe uses synthetic values |

## Verified on the production table (run `notices_20261023T175322Z`)

| Check | Result |
|---|---|
| `information_schema.column_masks` in `wbg_silver` | Exactly one mask: `procurement_notice_details.notice_text` → `workspace.wbg_silver.mask_restricted_notice_text` |
| Mask function body | Checks `is_account_group_member('wbg_restricted_notice_text')` |
| Reads by the current identity (owner) | 0 unmasked values; 20,169 placeholders; 136 NULLs (as in the source) |
| Current identity in the restricted group | No (account and workspace group checks both false) |
| Time travel on the table | Refused |
| Text fidelity (`notice_text_sha256` vs Volume source) | Equal for all 20,305 records |
| Other columns of the four tables | `notice_text` only in the details table; 0 e-mail addresses elsewhere |
| CHECK constraints | Not allowed on a masked table (`COLUMN_MASKS_CHECK_CONSTRAINT_UNSUPPORTED`); the 5 rules are in `silver.validated_rules` and verified with 0 violations |

## What Free Edition enforces (tested, synthetic data)

Probe `d14_access_probe_20261023T173456Z` created temporary objects in `workspace.default`, then dropped them; none
remain.

| Test | Result |
|---|---|
| Mask declared in `CREATE TABLE` | Accepted |
| Read by the current identity (also the owner): SQL, DataFrame API, `toPandas` | Placeholder only; NULL preserved |
| Derived values (`sha2`), filters (`LIKE '%example.org%'`) | Computed on the placeholder: no inference from the raw text |
| `CREATE TABLE … AS SELECT` copy | Copy contains only the placeholder |
| Time travel (`VERSION AS OF`) | **Refused** (`COLUMN_MASKS_FEATURE_NOT_SUPPORTED.TIME_TRAVEL`) |
| Managed-table storage location | Not exposed (empty), so a path-based read is not possible |
| `information_schema.column_masks` | Lists the mask |
| Stored value under the mask (positive control, pass-through mask) | Intact |

## Limitations (not enforced by Free Edition)

1. **Owner bypass.** The workspace has one user, who is the owner and an admin. The owner can `ALTER … DROP MASK` or
   change the function. The mask therefore guards against accidental exposure, not against a deliberate change by the
   owner; there is no separation of duties.
2. **Mask changes are not in Delta history.** `SET MASK` and `DROP MASK` do not appear in `DESCRIBE HISTORY`.
   *Corrected 2026-10-23 by the access audit:* they **are** recorded in `system.access.audit` as `unityCatalog` /
   `updateTables` events, and the probe's set/drop events were found there. The verifier also checks the mask through
   `information_schema` and the function body.
3. **Bronze and the files are not restricted.** The same text sits unmasked in:
   - Bronze `raw_record` (`wbg_bronze.bronze_procurement_notices_api`)
   - the Volume file `/Volumes/workspace/default/wbg_procurement_raw/bronze_inputs/procurement_notices_api/…jsonl`
   - local copies under `databricks_upload/bronze_inputs/procurement_notices_api/` and `discovery/samples/procnotices/`

   Bronze was not modified (standing rule). Restricting Bronze and the Volume (a mask on `raw_record`, Volume grants)
   needs a separate approval.
4. **No group exists yet.** Granting read access means creating account group `wbg_restricted_notice_text` and adding
   the RAG-preparation identity. That is an admin action that needs approval; until then, nobody can read the raw text
   through Silver.

## Requirements for downstream consumers

- **Frontend, external APIs, logs:** never select `procurement_notice_details.notice_text`. Use the IDs, references,
  `notice_text_sha256` and the flags.
- **Document/RAG preparation:**
  - Python only, running as a member of `wbg_restricted_notice_text` once that group is approved.
  - It decodes HTML (`html.unescape`, all entities) and normalises Unicode.
  - It removes or replaces e-mail addresses, phone and fax numbers and contact-person blocks.
  - It indexes only the sanitised text and writes no raw text to logs, caches, embeddings or the index.
  - Tests are required for HTML entities, Unicode, e-mail, phone and sanitisation.
- **Revisit when moving off Free Edition:** separate identities for pipelines, a group-managed reader role, audit logs,
  and a mask on Bronze `raw_record`.

---

# Security and Access Audit (2026-10-23, read-only)

**Scope.** The audit is read-only.
- **Databricks:** `SHOW`, `DESCRIBE`, `information_schema` queries, the system tables, and CLI list commands.
- **Local:** a file scan reporting counts and paths only.
- **Not printed:** any notice text, e-mail address or phone number.
- **Not changed:** no group, grant, mask, table or file.
- **Unchanged:** all 13 Silver tables and 17 Bronze tables (9 in `wbg_bronze`, 8 in `wbg_ref_bronze`).

## A1. Where unmasked notice text and contact data exist

**Databricks.** E-mail and phone counts below come from the shared detectors (`EMAIL_RE`, `PHONE_RE`) over every string
column of all 30 tables.

| Location | Contains | Unmasked? |
|---|---|---|
| `workspace.wbg_bronze.bronze_procurement_notices_api.raw_record` | Full notice JSON incl. `notice_text`: 20,305 records; e-mail in 4,425 rows, phone in 2,424 | **Yes** |
| Volume `/Volumes/workspace/default/wbg_procurement_raw/bronze_inputs/procurement_notices_api/procnotices_cluster_sample__cohort2000.jsonl` (88.6 MB) | Same 20,305 records (Bronze source file) | **Yes** (Volumes cannot be masked) |
| `workspace.wbg_silver.procurement_notice_details.notice_text` | Same text | No: column mask, verified |
| Other Silver notices tables | No notice text; 0 e-mail addresses | — |
| **Contract data (new finding, outside the original D-14 scope)**: Bronze `bronze_contract_awards_ds00005` (`Supplier`, `Contract Description`) → Silver `contract_award_lines.supplier_name` / `contract_description` and `contract_awards.contract_description` | **Supplier:** e-mail in 35 rows, phone in 39; together 58 supplier lines (42 suppliers, 21 projects; numeric STEP IDs; 22 of 58 look like firms). **Description:** e-mail in 6 rows (3 individual-consultant contracts) | **Yes** |
| DS00979 `Bid Description` (Bronze and Silver) | 0 e-mail; 4 phone-pattern hits, reviewed as equipment descriptions | n/a |
| Other volumes | `wbg_procurement_test`: only `connection-test.txt`; `wbg_reference_raw`: indicators, no notices; `raw_data`/`my_files`: not project data | n/a |
| Query history (`system.query.history.statement_text`) and audit (`request_params`) | SQL statements and parameters. No project job puts notice text in SQL literals (the probe used a synthetic value), but anything placed in a literal would be retained here | Risk only |

**Local machine.**

| Path | Notice records | E-mail matches |
|---|---|---|
| `discovery/samples/procnotices/procnotices_cluster_sample.jsonl.gz`: full Phase 1 pull, 276 projects, beyond the cohort | 30,791 with text | 15,533 |
| `databricks_upload/bronze_inputs/procurement_notices_api/procnotices_cluster_sample__cohort2000.jsonl` (git-ignored) | 20,169 | 9,805 |
| `discovery/samples/procnotices/procnotices_global_sample.jsonl.gz` | 767 | 1,789 |
| `discovery/project_deep_dive/raw/P163870/procnotices_page1.json`, `…/P178432/procnotices_page1.json` | 216 + 68 | 331 + 15 |
| Session scratchpad `…/scratchpad/notices/api.pkl`, `aw.pkl` (temporary pandas pickles created during the notices investigation) | all API / award notices | 9,805 + 2,188 |
| Claude Code transcript `~/.claude/projects/<this project>/bf719f1f-….jsonl` (31 MB) | Notice HTML fragments from early inspection output | 149 |
| `discovery/profiling/ml_label_evidence_candidates.csv`, `procnotices_*.profile.*` | Short passages / profile samples | 1–8 |
| DS00005 CSVs (`discovery/data_cache/…`, `databricks_upload/bronze_inputs/contract_awards_ds00005/…`) | Contract supplier and description contact data (see above) | 66 / 41 |
| Document corpus (`discovery/samples/documents/…`, deep-dive procurement plans) | WB documents with contact e-mails; future RAG input, so the same sanitisation applies | 1–15 per file |

Run reports, Silver code and tests contain the field name `notice_text` but no notice content; the tests and probe use
only synthetic `example.org` values. **Not git-tracked today**, but `.gitignore` excludes neither
`discovery/samples/procnotices/` nor `discovery/project_deep_dive/raw/`.

## A2. Who can read the original text today

| Principal | How | Can read raw notice text? |
|---|---|---|
| You (the only workspace user, 1 of 1) | Owner of schemas `wbg_bronze`, `wbg_silver`, `wbg_ref_bronze`, all their tables, all five volumes and the mask function. Member of workspace `admins`, so also of the catalog-owner group `_workspace_admins_workspace_…` | **Yes:** Bronze `raw_record` and the Volume file, through ownership. **No** through Silver (mask; not in `wbg_restricted_notice_text`) |
| `_workspace_users_workspace_…` / `users` | `USE CATALOG workspace`; create rights in `workspace.default` only | No: no `USE SCHEMA` or `SELECT` on `wbg_*`, no `READ VOLUME` |
| `account users` | `USE SCHEMA` on `information_schema` only | No |
| Service principals | None exist (0) | — |
| Personal access tokens, apps, jobs, pipelines, vector-search endpoints | None (0 of each) | — |
| Databricks platform identities seen in the audit log (last 7 days) | `f4cbcf34…`: platform driver (predictive optimisation / maintenance); obtained temporary table credentials on the notice tables 8 times. `080486a9…`: Catalog Explorer UI calls. `adff94ba…`: schema updates | Platform maintenance only; not controllable by workspace grants |
| SQL warehouse | 1 (`Serverless Starter Warehouse`), runs as the calling user | Same as the caller |
| Model serving | 12 Databricks-hosted foundation-model endpoints (LLM and embedding) | Not by themselves, but any `ai_query`, agent or embedding job run over raw text would send it there |

**Explicit grants on `wbg_*` objects: none.** All access comes from ownership and admin membership.

## A3. What Free Edition supports (evidence level)

| Control | Status |
|---|---|
| Column masks (UC) | **Tested and enforced** (probe plus production table), including for the owner |
| Mask changes audited | **Yes:** `system.access.audit` (`updateTables`) is available. Not in Delta history |
| `GRANT`/`REVOKE`, `SHOW GRANTS` on catalog/schema/table/volume/function | `SHOW GRANTS` works. `GRANT`/`REVOKE` are standard UC statements but **not executed** (not approved). They cannot restrict an owner or admin |
| Row filters | `information_schema.row_filters` exists (none defined); not needed here |
| Volume-level restriction | `READ VOLUME`/`WRITE VOLUME` grants and ownership only. **Files cannot be masked**, and an owner or admin always retains access |
| Account groups / service principals | Groups `admins`, `users` and a Databricks-created clone exist; 0 service principals. Creating an account group or service principal on Free Edition is **not yet tested** (creation was not approved). The first implementation step tests it |
| Audit / lineage / query history system tables | Available (`system.access.audit`, `system.access.table_lineage`, `system.query.history`); retention not verified |

## A4. Proposed permission changes (smallest safe set; nothing done yet)

Ordered so that each step can be applied and verified on its own, and reversed by its stated inverse.

**Step 0: local and tooling (no Databricks change)**

- **0a. Claude Code deny rules** in a new project `.claude/settings.json`:
  - `Read`/`Grep` deny for `discovery/samples/procnotices/**`,
    `databricks_upload/bronze_inputs/procurement_notices_api/**` and `discovery/project_deep_dive/raw/*/procnotices_*`.
  - This blocks the file tools from displaying raw notice files. Python jobs that read them in memory still run.
  - *Inverse:* remove the rules.
- **0b. File permissions:** `chmod 600` on the raw local notice files, and add `discovery/samples/procnotices/` and
  `discovery/project_deep_dive/raw/` to `.gitignore`. *Inverse:* `chmod 644` and remove the lines.
- **0c. Delete the two scratchpad pickles** (`api.pkl`, `aw.pkl`). They are temporary investigation artefacts, and
  reproducible. *No inverse needed.*
- **0d. Transcript.** Your decision: keep, or delete this session's transcript after the work ends. Option: set
  `cleanupPeriodDays` in `~/.claude/settings.json`.

**Step 1: identity**

- **1a.** Create account group `wbg_restricted_notice_text`, **empty**. If Free Edition allows only workspace-local
  groups, the mask functions switch from `is_account_group_member` to `is_member`, which needs re-verification.
  *Inverse:* delete the group.
- **1b. (Optional, preferred.)** Create service principal `wbg-notice-sanitizer` as the only future member. *Test
  first:* whether Free Edition allows service principals and OAuth M2M for Databricks Connect. If not, see limitation
  L2.

**Step 2: Bronze table (metadata only; data and Delta version unchanged)**

- **2a.** Create mask function `workspace.wbg_bronze.mask_restricted_notice_raw_record(raw STRING)`.
  - **Members** get `raw` unchanged.
  - **Everyone else** gets `raw` with only the JSON value of `notice_text` replaced by the D-14 placeholder (escape-aware
    regex), so all other fields stay readable.
- **2b.** `ALTER TABLE wbg_bronze.bronze_procurement_notices_api ALTER COLUMN raw_record SET MASK …`.
  - A synthetic copy is tested first to confirm the table version does not change and the JSON stays valid.
  - *Inverse:* `DROP MASK`.
- **Side effect (accepted):** time travel on that Bronze table is refused, and CHECK constraints become impossible
  (none exist).

**Step 3: Volume file**

- **3a.** Create schema `workspace.wbg_restricted` with volume `raw_notice_text`, and move the one notices JSONL there
  from `wbg_procurement_raw`. The other Bronze input files stay where they are.
- **3b.** Transfer ownership of `wbg_restricted` to `wbg_restricted_notice_text`, and grant `READ VOLUME` only to that
  group. *To test:* whether catalog-owner or admin membership still implies read. If it does, this is a guardrail only
  (limitation L1).
- **3c.** Update `bronze_inputs_manifest.json` (the volume path) and the notices verifier (below).
- *Inverse:* move the file back and drop the empty schema.

**Step 4: code (needed by steps 2–3 so verification keeps working)**

- **Runner:** `run_notices_silver` refuses to rebuild from Bronze when `raw_record` is masked for the caller. This
  prevents placeholders from being written into Silver.
- **Source-file verification:** `verify_jsonl_source_file` and `verify_notices_silver` compare *redacted* forms, using
  the same placeholder substitution on the Volume lines. Non-members can then still verify every other field, the record
  counts and the SHA-256 of the redacted records.
- **Full-text fidelity:** checked only by a group member, the sanitiser.

**Step 5: contract contact data (new finding; separate decision suggested, D-14b)**

- **Do not mask the Silver contract tables now.** Adding masks would conflict with their existing CHECK constraints and
  break supplier analytics.
- **Instead:** strip e-mail and phone substrings from `supplier_name` and `contract_description` in the Gold/serving
  layer (`supplier_name_display`) and in the RAG sanitiser. Keep the published values in Silver.
- **Alternative:** a redaction mask on Bronze `Supplier` / `Contract Description` (Bronze has no constraints).

## A5. Impact on the Databricks Connect and Claude Code workflow

| Workflow | Impact of steps 0–4 |
|---|---|
| WDI, income class, WGI, CPIA, projects, contracts Silver jobs and verifiers | None: they never read the notices API Bronze table or file |
| Notices Silver re-runs | Blocked for non-members by design (step 4). The current tables need no re-run |
| Notices verification (`verify_notices_silver`) | Keeps working on redacted comparisons; full-text hash check only for a group member |
| `DESCRIBE HISTORY`, row counts, `information_schema` checks used in baselines | Unaffected (verified on the masked Silver table) |
| Time travel on masked tables | Refused; no project job uses it |
| Predictive optimisation (platform) | Unaffected |
| Claude Code | File tools can no longer display raw notice files (0a). Databricks reads as you return placeholders for notice text. Debugging raw text needs a recorded, time-boxed group membership |

## A6. Roles for `wbg_restricted_notice_text` and the sanitisation workflow

| Role / identity | Members | Privileges |
|---|---|---|
| `wbg_restricted_notice_text` (account group) | Only `wbg-notice-sanitizer` (service principal). Break-glass: a named human for a recorded, time-boxed task, removed afterwards and reviewed in `system.access.audit` | `USE CATALOG workspace`; `USE SCHEMA` on `wbg_bronze`, `wbg_silver`, `wbg_restricted`; `SELECT` on `bronze_procurement_notices_api` and `procurement_notice_details`; `READ VOLUME` on `wbg_restricted.raw_notice_text`; `USE SCHEMA`, `CREATE TABLE`, `MODIFY` on the output schema (proposed `wbg_sanitized`) |
| Developer (you) | — (not a member) | Owner as today; reads placeholders; reads sanitised outputs |
| `wbg-rag-indexer` (future service principal) | — | `SELECT` on sanitised tables only; vector-index create/query. No `wbg_bronze`, no `wbg_restricted` |
| `wbg-app-api` (future service principal) | — | `SELECT` on Gold/serving tables and vector-index query only |

**Sanitisation workflow** (future; Python; runs as `wbg-notice-sanitizer`):

1. Read raw text.
2. Decode with `html.unescape` (all entities), Unicode NFKC, and HTML to text.
3. Redact:
   - e-mail addresses, including obfuscated forms (`name [at] domain`, `(at)`, `arobase`)
   - phone, fax and mobile numbers in EN/FR/ES/PT label forms, plus international formats
   - contact-person blocks (`Attn`, `Contact`, `Personne de contact`, `Persona de contacto`)
   - `mailto:` links
4. Re-scan the output with independent detectors. **Any hit is blocking**: the record is quarantined by ID and its text
   is never written.
5. Write `notice_text_sanitized`, `sanitizer_version`, redaction counts by type and `source_text_sha256`.
6. Produce a run report with counts only.
7. **Tests:** HTML entities (named, numeric, double-escaped); Unicode (NFKC, no-break and zero-width spaces, fullwidth
   `＠`); e-mail and phone positives; false-positive guards (`approximately`, `Intel`, `station mobile`, dates, amounts,
   reference numbers); idempotence; no raw text in logs or exceptions.

## A7. Keeping e-mails and phone numbers out of embeddings, indexes, APIs, logs and AI responses

| Channel | Control |
|---|---|
| Embeddings and vector indexes | Built **only** from sanitised columns, by an identity without access to raw text, so the mask returns placeholders anyway. The pre-embedding chunk gate re-runs the detectors and rejects any hit. Index metadata holds IDs and hashes, never raw text |
| Databricks model endpoints (`ai_query`, agents) | Rule and code review: no AI function over `raw_record`, `notice_text` or contract contact columns. Only sanitised text leaves the workspace |
| Application APIs | Allow-listed response schemas without `notice_text`, `raw_record` or contact columns. An output redaction filter (same detectors) runs on every response field. Integration test: no e-mail or phone in any API fixture response |
| AI responses (RAG answers) | Retrieval returns sanitised chunks only. A post-generation redaction filter runs on the answer, and citations use document IDs and URLs |
| Logs, reports, query history | Structured logging with a redaction filter; jobs log counts and IDs only (current practice); no row payloads in exceptions; no data values in SQL literals |
| Claude Code sessions | Deny rules (0a); never print restricted columns; inspect with counts and IDs only |
| Contract supplier and description contact data | Redacted in the Gold display fields and in the sanitiser (step 5) |

## A8. Limitations that Free Edition cannot address

- **L1. One human, who is owner and admin.** Grants and ownership changes cannot stop you. Masks can be dropped by you;
  this is visible in `system.access.audit`, but not prevented. Controls are guardrails against accidental exposure, not
  separation of duties.
- **L2. Service principals untested.** If Free Edition cannot create a service principal or use one with Databricks
  Connect, the sanitiser has to run as you with temporary group membership. That weakens isolation; record each use in
  the audit log.
- **L3. Files cannot be masked.** The Volume file stays readable by any owner or admin, and so do the local copies.
  Protection relies on ownership, `READ VOLUME`, file permissions and tooling rules.
- **L4. Feature conflicts.** Masked tables refuse time travel and CHECK constraints.
- **L5. Platform features absent.** Enterprise controls such as customer-managed keys, Private Link, IP access lists and
  compliance profiles are not part of Free Edition (not tested here). Storage is Databricks-managed.
- **L6. Platform service identities** (predictive optimisation) read table files for maintenance, outside workspace
  grants.
- **L7. Text already exposed in this session.** Notice text fragments were shown during the early notices investigation
  (sample record output), so they are in this session's transcript and were processed by the model. This cannot be
  undone; from then on, outputs were counts and IDs only.
- **L8. Audit retention.** The system-table retention period was not verified.

---

# Step 0 Implemented (2026-10-23, approved with safeguards)

| Safeguard | Result |
|---|---|
| Git tracking check | **No Git repository** exists in the project or any parent folder (`git rev-parse` fails everywhere; no `.git` directory). No sensitive file is or was tracked; no history to clean |
| `.gitignore` | Added to `procurement-intelligence/.gitignore`: `discovery/samples/procnotices/*.jsonl.gz` and `discovery/project_deep_dive/raw/*/procnotices_*.json`. `databricks_upload/bronze_inputs/*` was already ignored. Verified with `git check-ignore` in a throwaway scratch repo: the 4 raw files are ignored; manifests, `cluster_projects.txt`, other deep-dive files and code stay trackable |
| Claude Code restrictions | New project file `.claude/settings.json` (session root) with `Read` deny rules for exactly the 3 raw-notice path patterns, plus a synthetic canary `.claude/d14_canary/**`. Tested: reading the canary is refused ("denied by your permission settings"); a non-sensitive file in the same folder (`cluster_projects.txt`) still reads |
| File permissions | `chmod 600` (was 644) on the 5 raw notice files. Pre-check: all files owned by the only interactive account on this Mac, which also runs Python jobs, Claude Code and VS Code. SHA-256 of all 5 files identical before and after, and equal to their manifests (samples, Bronze inputs, deep-dive) |
| Scratchpad pickles | `api.pkl` (20,305 rows) and `aw.pkl` (16,189 rows) verified as derived copies: every original field equals the Bronze-input JSONL record, and every derived column (`nid`, `ref_norm`, `ver`, `ref_txt`, `awarded_ids`, `step`) is reproduced exactly by `wbdisc`. Then deleted. Other scratchpad files were kept |
| Not changed | Claude Code transcript (retained), source datasets (contents unchanged), all 17 Bronze tables, 13 Silver tables, the cohort, Databricks permissions and masks |
| Regression | Silver tests 42/42 and discovery tests 129/129 pass. mypy: 1 pre-existing error in `discovery/wbdisc/countries.py` (unchanged since 2026-10-20; reached through the projects import), none in the changed code |

**Remaining local limitations**
- **Bash can still read the files.** The deny rules cover Claude Code's file-read tools only; Bash commands such as
  `cat` or Python can still read the files. That is needed for the jobs, so it is guarded by practice (counts and IDs
  only), not enforced.
- **`chmod` protects only against other local accounts**, and there are none today. It does not protect against the
  owner or processes running as the owner.
- **Derived reports with contact fragments are not restricted:** `ml_label_evidence_candidates.csv` and the
  `procnotices_*.profile.*` files. They belong to the paused ML investigation, so restricting them would interfere with
  it.
- **The transcript stays**, by your decision.

# Steps 1–4: Coordinated Implementation Plan (not approved; nothing executed)

## Can Free Edition support a separate sanitisation identity? (read-only evidence)

| Check | Result |
|---|---|
| Your role | Member of `admins` (workspace admin); no extra entitlements needed for identity management APIs |
| Workspace service-principal API (`service-principals list`) | Reachable; returns an empty list (no error) |
| Workspace-level service-principal OAuth secrets (`service-principal-secrets-proxy`) | Present in CLI v1.22.1 (needed for OAuth M2M Databricks Connect) |
| Account-level APIs from the workspace profile | Not reachable (`Not Found`); account console not available through this profile |
| Groups | `admins`, `users`, Databricks-created clone; workspace group API reachable |

**Conclusion:** a separate service principal is **likely but unproven**. The APIs exist and you hold the needed role,
but only creating one proves Free Edition allows it, including an OAuth secret and use with Databricks Connect. Whether
a workspace-created group satisfies `is_account_group_member` (the function the mask uses) or only `is_member` must also
be tested.

## Gate tests (each needs approval; each is reversible; only synthetic data)

| # | Test | Pass criterion | If it fails |
|---|---|---|---|
| T1 | Create service principal `wbg-notice-sanitizer` (workspace API) | SP listed | Fallback F1: break-glass human membership (weaker; audited) |
| T2 | Create its OAuth secret. Add a local profile `wbg-sanitizer` to `~/.databrickscfg` (secret never printed). Databricks Connect Serverless: `SELECT current_user()` | Returns the SP's application ID | F1 |
| T3 | Create group `wbg_restricted_notice_text`; add only the SP | `is_account_group_member` (or `is_member`) true for the SP and false for you | Change the mask functions to the working membership function, and re-verify the Silver mask |
| T4 | Synthetic masked table in `workspace.default` (probe pattern), owned by you, `SELECT` granted to the group | SP reads raw synthetic text; you read the placeholder | Stop: the restriction design is not viable |
| T5 | Synthetic copy of the Bronze notice schema (synthetic JSON with escaped quotes, Unicode and U+2028). Apply the proposed `raw_record` redaction mask | JSON valid for non-members; `notice_text` placeholder only; other fields unchanged; `DESCRIBE HISTORY` version unchanged by `SET MASK`; time travel refused | Fix the regex, or use a NULL-text JSON rebuild |
| T6 | Synthetic schema and volume owned by the group, `READ VOLUME` granted only to the group | Determine whether you, as admin and catalog-owner group member, can still list/read the file | Document as a guardrail only (L1) |
| T7 | Drop all T4–T6 synthetic objects; keep T1–T3 identities if passed | Nothing left in `workspace.default` | — |

## Implementation sequence (after the gates pass; each step approved separately)

1. **I1. Identities:** keep the SP and group from T1–T3. Document the break-glass procedure: add a named human, record
   the reason, remove them, check `system.access.audit`.
2. **I2. Code first, before any data-side change:**
   - **Shared redaction module** `silver/common/redaction.py`: one placeholder; a Python redaction and the equivalent
     SQL expression; parity tests on synthetic JSON.
   - **Runner guard:** `run_notices_silver` refuses to run unless the caller can read unmasked `raw_record`, so
     placeholders can never be written into Silver.
   - **Source-file verification:** `verify_jsonl_source_file` gains a `redact=True` mode that compares redacted Volume
     lines to the redacted Bronze values.
   - **Verifier:** `verify_notices_silver` runs the full text-hash checks only for group members, otherwise redacted
     comparisons.
   - **Regression:** all tests, plus a dry run of the notices job **as you**. It must refuse cleanly once Bronze is
     masked, and still pass today.
3. **I3. Bronze mask:**
   - Create the function `wbg_bronze.mask_restricted_notice_raw_record`, then `ALTER … SET MASK` on
     `bronze_procurement_notices_api.raw_record`.
   - Verify:
     - table version and row count unchanged
     - `information_schema.column_masks` lists the mask
     - placeholder-only `notice_text` for you
     - the SP reads raw text
     - `verify_notices_silver` passes in redacted mode
     - all other Bronze and Silver tables unchanged
   - *Rollback:* `DROP MASK` and drop the function.
4. **I4. Volume:**
   - Create `workspace.wbg_restricted` with volume `raw_notice_text`.
   - Copy the notices JSONL and verify its SHA-256 equals the manifest.
   - Update `bronze_inputs_manifest.json` with the new path, recorded as a manifest change.
   - Only after verification passes from the new location, remove the old copy from `wbg_procurement_raw`. That
     deletion needs explicit approval.
   - Set the ownership and grants from T6.
   - *Rollback:* copy the file back and revert the manifest.
5. **I5. D-14b (optional, with I3):** Bronze DS00005 redaction masks on `Supplier` and `Contract Description` (option
   B), with the contracts verifier switched to redacted comparisons for non-members.
6. **I6. Final verification:**
   - the full notices and contracts verifiers
   - the access probe re-run, including the SP path
   - a write-free audit-log check
   - D-14 documentation updated with evidence

## Effect on the Databricks Connect and Claude Code workflow (after I1–I4)

- **Your profile (`wbg-databricks-free`):** sees placeholders in Bronze and Silver notice text. All other Silver jobs
  are unchanged.
- **Notices rebuilds and full-text verification:** run only under the `wbg-sanitizer` profile. That run must be
  explicit, and its outputs are counts only.
- **Claude Code:** keeps working with your profile. Using the SP profile from Claude Code needs approval per task, and
  output stays counts and IDs only.

---

# Gate Tests T1–T7 (approved and run 2026-10-23; synthetic data only)

**Harness:** `silver/notices/d14_gate_tests.py`.
- Temporary identities and objects are named `wbg-d14-test-*`, `wbg_d14_test_*` and `zz_wbg_d14_gate_*`, and recorded in
  a ledger as they are created.
- Cleanup runs in `finally`.
- The test group deliberately did **not** use the production name `wbg_restricted_notice_text`, which would have
  unmasked the production Silver text for the test service principal.
- Secrets were held in memory only. A scan of the run folders and logs found 0 secret-like strings.

**Runs**

| Run | Outcome |
|---|---|
| `d14_gate_tests_20261023T210613Z` | Harness bug in the service-principal session builder; stopped at T2; full cleanup |
| `d14_gate_tests_20261023T211026Z` | Harness bug; stopped at T2; full cleanup |
| `d14_gate_tests_20261023T211406Z` | **Valid run**: stopped at T3 on unexpected platform behaviour, as instructed; full cleanup |

**Harness lesson (Databricks Connect):** with `sdkConfig`, serverless must be set as `Config(serverless_compute_id="auto")`.
Calling `.serverless(True)` raises "sdkConfig must not be set when connection parameters are explicitly configured".
This was confirmed offline with a dummy credential before the third run.

## Results

| Test | Result | Evidence |
|---|---|---|
| P0 pre-checks | **Pass** | You are in `admins`; 0 existing service principals; no name collisions; baseline of 30 table versions, masks, schemas, identities and cohort SHA saved |
| T1 create service principal | **Pass** | Created via the workspace SCIM API. Defaults: entitlements `workspace-access` and `databricks-sql-access`; **no groups, no roles, not admin** |
| T2 OAuth secret + Databricks Connect as the SP | **Pass** | Workspace-level SP secret created (never printed or stored). Databricks Connect **Serverless** session as the SP via OAuth M2M on the first attempt; `current_user()` = the SP's application ID; not in `admins` |
| T2 least privilege | **Pass** | The SP is **denied** (`INSUFFICIENT_PERMISSIONS`) on `wbg_bronze.bronze_procurement_notices_api`, `wbg_silver.procurement_notice_details` and `wbg_silver.contract_award_lines` |
| T3 group membership | **Fail** | The group was created via the workspace SCIM API and the SP was added (SCIM shows it as the only member). Unity Catalog does not recognise it: `is_account_group_member` and `is_member` both stay **false** for the SP after about 2 minutes, and `GRANT … TO <group>` fails with `PRINCIPAL_DOES_NOT_EXIST`. Workspace-created groups are not Unity Catalog principals here, and account-level group APIs are not reachable from this workspace profile (`Not Found`) |
| T4 mask + grants via the group | **Not run** (blocked by T3; stopped as instructed) | — |
| T5 Bronze-style `raw_record` redaction mask | **Not run** (blocked by T3) | — |
| T6 restricted volume ownership and grants | **Not run** (blocked by T3) | — |
| T7 cleanup and production unchanged | **Pass** | Cleanup ran in all three runs: SP secret, SP, group, synthetic tables and functions deleted. Leftovers: none (tables, schemas, functions, SPs, groups). State after = baseline before: all 30 table versions, `information_schema.column_masks` (still only the production Silver mask), schemas, identities and cohort SHA-256 |

## Feasibility of a separate sanitisation identity

- **Feasible:** a separate, non-admin service principal that authenticates to Databricks Connect Serverless (T1, T2)
  and has no access to production tables by default.
- **Not feasible as designed:** group-based access through `wbg_restricted_notice_text`. Groups created from this
  workspace are not Unity Catalog principals, so neither grants nor membership functions work with them (T3).
- **Proposed alternative, not yet tested; needs approval:**
  - **T3b:** grant privileges directly to the SP, which Unity Catalog addresses by application ID, on a synthetic table.
  - **T4b:** a mask keyed on identity, `CASE WHEN current_user() IN (<allow-listed SP application IDs>) THEN t ELSE
    '<placeholder>' END`, ideally through one governance function so the allow-list lives in one place.
  - **T5 and T6** then run unchanged, but with grants to the SP.

## Remaining access-control limitations (Free Edition, observed)

1. **No account-level groups from this workspace.** Role-based (group) access control is unavailable. Access must be
   granted per identity, and masks must allow-list identities.
2. **The owner-admin bypass remains.** You can change masks, grants or the allow-list (audited in
   `system.access.audit`, not prevented).
3. **Files cannot be masked.** Restricting volumes (T6) is still untested.
4. **Service-principal secret management is manual.** Secrets are workspace-level and have to be stored locally for
   Databricks Connect (e.g. a `~/.databrickscfg` profile with mode 600, or the OS keychain) and rotated by hand.
   Platform secret scopes can't hold them for a local client.
5. **Masks rule out other features:** no time travel, no CHECK constraints.
6. **Bronze, the Volume file and the local raw copies remain unrestricted** until the follow-up steps are approved.

## Effect on Databricks Connect

- **Your profile (`wbg-databricks-free`) is unaffected.** All Silver jobs keep running as you.
- **A second profile for the SP works:** OAuth M2M + `serverless_compute_id="auto"`. Jobs needing raw text (sanitiser,
  full-text verification) would run under it explicitly.
- **Two identities in one process are possible** using a fresh `DatabricksSession.Builder()` per identity; the shared
  `DatabricksSession.builder` keeps the first profile's configuration.

## Recommended security architecture for the PoC

| Layer | Control |
|---|---|
| Identities | **You:** developer and owner; read placeholders for restricted text. **`wbg-notice-sanitizer` SP:** the only identity allow-listed for raw notice text; OAuth M2M; secret stored locally with mode 600, rotated. **Future `wbg-app` SP:** `SELECT` only on Gold/serving tables and the vector index |
| Restricted reader logic | One governance function, e.g. `workspace.wbg_governance.is_restricted_reader()` = `current_user() IN (<SP application IDs>)`, used by every restricting mask. Changes are reviewed through `system.access.audit` |
| Bronze | Redaction mask on `bronze_procurement_notices_api.raw_record` (replaces only the `notice_text` value). The DS00005 `Supplier` / `Contract Description` contact-substring redaction (D-14b B) is optional. Only after T3b–T5 pass |
| Silver | The existing `notice_text` mask switched to the governance function. Contract tables unchanged (D-14b A handles display) |
| Raw files | The notices JSONL moved to a restricted volume with `READ VOLUME` for the SP only (after T6 shows how much it protects). Locally: Step 0 controls |
| Sanitised text | A sanitiser job **as the SP**: Python decode → redact → independent re-scan (blocking) → `wbg_sanitized.notice_text_sanitized` plus counts-only reports |
| Gold / serving | D-14b A redaction of display fields; supplier identity policy (D-14b); allow-listed columns |
| RAG / AI / API | Index only sanitised text; pre-embedding detector gate; output redaction filter on API and RAG answers; no `ai_query` over raw columns |
| Monitoring | Scheduled audit-log query for `updateTables` / grant changes on restricted objects; the D-14 probe and gate tests kept as regression checks |
| Accepted for the PoC | Owner-admin bypass and no account groups. For production: an enterprise workspace with account-level groups, separation of duties and enterprise network/key controls |

**Next approval needed:** gate tests **T3b–T6b** (direct SP grants and an identity-keyed mask; synthetic only; same
safeguards). If they pass, each implementation step I1–I6 still needs approval separately.

---

# Gate Tests T3b–T6b (approved and run 2026-10-23; synthetic data only)

**Harness:** `silver/notices/d14_gate_tests_b.py`. Run `d14_gate_tests_b_20261023T213951Z`.

**Setup.**
- **Temporary catalog:** all objects live in `zz_wbg_d14_gateb_20261023t213951z`. The service principals have no
  `USE CATALOG` on `workspace`, and granting it would have changed production permissions.
- **Two temporary non-admin service principals:**
  - `reader`: granted, and on the allow-list.
  - `granted_only`: granted, but **not** on the allow-list.
- **You:** the owner of everything, not on the allow-list.
- **Synthetic data:** the text carries a random sentinel. Outputs report booleans and counts only. A scan of the
  outputs found 0 secret-like strings, 0 sentinels and 0 e-mail addresses.

## Results (individual checks)

| Area | Check | Result |
|---|---|---|
| T1b identity | 2 SPs created: no groups, no roles, not admin | **Pass** |
| T1b identity | Databricks Connect Serverless as each SP (OAuth M2M) | **Pass** |
| T1b identity | `current_user()` = `session_user()` = the SP's **application ID**, exactly, as a lower-case UUID. Your `current_user()` is your e-mail login | **Pass** (format verified, not assumed) |
| T1b identity | Minimum entitlement for Databricks Connect Serverless | **`workspace-access` only.** Removing `databricks-sql-access` still works; removing `workspace-access` breaks the connection |
| T1b identity | Both SPs denied on the production tables `bronze_procurement_notices_api`, `procurement_notice_details`, `contract_award_lines` | **Pass** (`INSUFFICIENT_PERMISSIONS`) |
| T3b direct grants | `GRANT … TO \`<application ID>\`` accepted (USE CATALOG / USE SCHEMA / SELECT on temporary objects) | **Pass** |
| T3b direct grants | A table without a grant is denied to both SPs | **Pass** |
| T4b identity masks | Governance-function mask (`is_restricted_reader()` = `current_user() IN (<reader>)`): `reader` RAW, `granted_only` PLACEHOLDER, you PLACEHOLDER | **Pass** |
| T4b identity masks | Inline allow-list mask: same three outcomes | **Pass** |
| T4b identity masks | Allow-list is exact: an upper-case or space-padded ID does not match; NULL preserved; the governance function is false for you | **Pass** |
| T4b paths | **Filters:** `LIKE` on the sentinel matches 0 rows for you and `granted_only`, 2 for `reader` | **Pass** |
| T4b paths | **Derived values:** `sha2` equals raw for 0 rows for you and `granted_only`, 2 for `reader` | **Pass** |
| T4b paths | **DataFrame reads:** placeholder for you and `granted_only`; raw for `reader` | **Pass** |
| T4b paths | **Your exports:** `toPandas` shows no sentinel; CTAS and `INSERT … SELECT` copies hold placeholders only; a JSON export to a volume file has no sentinel | **Pass** |
| T4b paths | `granted_only` cannot CTAS (no `CREATE TABLE`; `PERMISSION_DENIED`) | **Pass** |
| T4b paths | **Through a view you own:** `reader` RAW, `granted_only` PLACEHOLDER. The mask follows the querying identity, so a view is not a bypass | **Pass** |
| T4b paths | Calling the mask function directly returns the placeholder for you | **Pass** |
| T5b Bronze-style JSON | `SET MASK` leaves the Delta version unchanged (1 → 1) | **Pass** |
| T5b Bronze-style JSON | Non-members (you, `granted_only`): valid JSON; every non-sensitive field unchanged, including a decoy `"notice_text": "…"` string inside another field; `notice_text` placeholder; NULL kept; sentinel absent | **Pass** |
| T5b Bronze-style JSON | Python redaction gives byte-identical results, so verifiers can compare redacted forms | **Pass** |
| T5b Bronze-style JSON | `reader` gets exactly the raw records (SHA-256 multiset equal) | **Pass** |
| T9 side effects | Time travel on a masked table | Refused (`COLUMN_MASKS_FEATURE_NOT_SUPPORTED.TIME_TRAVEL`) |
| T9 side effects | Adding a CHECK constraint to a masked table | Refused (`COLUMN_MASKS_CHECK_CONSTRAINT_UNSUPPORTED`) |
| T9 side effects | Adding a mask to a table that has a CHECK constraint | Refused (`COLUMN_MASKS_FEATURE_NOT_SUPPORTED.CHECK_CONSTRAINT`). This confirms D-14b option C is blocked |
| T9 side effects | `DESCRIBE HISTORY` (mask changes not listed), `DESCRIBE DETAIL`, row count | Work |
| T6b volume | `reader` with `READ VOLUME` reads the file; `granted_only` without it is denied | **Pass** |
| T6b volume | You, as owner of catalog, schema and volume | Read ok |
| T6b volume | After **volume** ownership moved to `reader` | You can **still read** (parent ownership) |
| T6b volume | After **schema** and then **catalog** ownership moved to `reader` | You are **denied**; self-granting `READ VOLUME` and re-taking catalog ownership are also **denied**. You are not metastore admin (the metastore owner is "System user") |

## Cleanup incident and recovery (reported in full)

- **What went wrong:** the T6b ownership transfers made the temporary catalog invisible to you (`SHOW CATALOGS` hides
  it). The harness's cleanup trusted `SHOW CATALOGS`, skipped the drop, and then deleted the SPs. That left the
  synthetic catalog **orphaned**, owned by a deleted SP.
- **Recovery** (same day, synthetic objects only):
  1. Waited about 15 minutes for the audit log to ingest the run's events.
  2. Found the test SPs' application IDs in the audit log. I excluded one platform identity that predates the tests,
     identified by its earlier events and lack of a Python client.
  3. Re-added the owning SP to the workspace by application ID and created a temporary secret.
  4. Had it return catalog, schema and volume ownership to you, then dropped the catalog as you.
  5. Deleted the recovery secret and workspace assignment.
- **Confirmation:** `catalogs.get` reports the catalog absent.
- **Harness fixed:** cleanup now returns ownership first, drops regardless of visibility, confirms absence through the
  API, and keeps the owning SP if absence is not confirmed.

## Cleanup confirmation and production state

- **Removed:** both SP secrets, both workspace SP assignments, both recovery assignments and secrets, and the temporary
  catalog with its schema, tables, view, functions, volume and files.
- **State after = pre-test baseline:** all 30 Bronze/Silver table versions, `information_schema.column_masks` (only the
  production Silver mask), catalogs (via the API), `workspace` schemas, grants on the `workspace` catalog, workspace
  service principals and groups, and the cohort SHA-256.
- **Residual objects that cannot be removed from this workspace:** deleting an SP from the workspace removes only its
  **workspace assignment**; the account-level identity persists (proven by re-adding it by application ID). The test
  SPs from today's gate runs remain as account-level identities, and account APIs are unreachable here (`Not Found`):
  - 3 from T1–T7 and 2 from T3b–T6b
  - each has no workspace assignment, no secrets (all deleted) and no grants (their objects are dropped)
  - removing them needs account-console access, which this Free Edition workspace does not expose

## Answers

- **Do direct SP grants work?** Yes. The `<application ID>` is the Unity Catalog principal, and grants on catalog,
  schema, table, view and volume behave as least privilege.
- **Do identity-based column masks enforce the intended restrictions?** Yes, for every read path tested (SQL,
  DataFrame, `toPandas`, filters, hashes, CTAS/INSERT copies, file export, views). Only the allow-listed SP saw raw
  synthetic text; a granted but not allow-listed SP and the owner saw the placeholder.
- **Can raw Volume access be meaningfully restricted?**
  - **Against other identities:** yes, through `READ VOLUME`.
  - **Against you:** only by giving up schema and catalog ownership to an SP, after which you cannot re-take it (you
    are not metastore admin). That **does** lock you out, but it is fragile: it makes the objects invisible and needs
    the SP to hand ownership back. Moving only volume ownership does **not** restrict you.
- **Practical controls for the Free Edition PoC:**
  - a dedicated non-admin sanitiser SP with only `workspace-access`, used through a separate Databricks Connect profile
  - direct grants to that SP
  - masks driven by one governance function allow-listing SP application IDs
  - the Bronze `raw_record` JSON redaction mask: metadata-only, no version change
  - redacted-form verification in Python
  - `READ VOLUME` limited to the SP for raw files
  - Step 0 local controls
  - audit-log monitoring
- **Not recommended for the PoC:** transferring catalog or schema ownership away from you. It works as a lock-out, but
  it risks orphaned objects, and recovery depends on the SP.

## Remaining security gaps

1. **You keep full control** of the production `workspace` objects: you can edit the allow-list function, drop masks
   and grant yourself access. Changes appear in `system.access.audit` with about 15 minutes' ingestion lag, but are not
   prevented. There is no separation of duties.
2. **No group-based roles**, so the allow-list and grants are managed per identity.
3. **Account-level SP lifecycle is out of reach:** test SPs persist at account level and can't be fully deleted here.
4. **SP secrets are workspace secrets**, stored locally for Databricks Connect and rotated manually.
5. **Masked tables lose features:** no time travel and no CHECK constraints. Masks can't be added to the existing
   constrained Silver contract tables.
6. **Bronze `raw_record`, the Volume JSONL and the local raw copies remain unrestricted** until the I-steps are
   approved.
7. **The audit log lags about 15 minutes**, so monitoring is near-real-time at best.

---

# Status 2026-10-23: production implementation paused (internal PoC)

- **Paused:** implementation steps I1–I6 (D-14) and Option B (D-14b) are paused because the platform is an internal PoC.
- **In force, unchanged:**
  - the Silver `notice_text` column mask
  - the Step 0 local safeguards (`.gitignore`, Claude Code deny rules, `chmod 600`)
  - all permissions
  - D-14b Option A and the supplier identity policy, which apply when Gold/API/RAG outputs are built
- **For future production hardening:** the gaps are listed under "Remaining security gaps" and "A8. Limitations"
  above. Before any external exposure, at minimum:
  1. the sanitiser SP with identity-keyed masks (I1–I3)
  2. a restricted volume for the raw notices file (I4)
  3. the D-14b Bronze redaction mask
  4. account-level identity management (removal of the test SPs that persist at account level)
  5. an enterprise workspace for separation of duties
